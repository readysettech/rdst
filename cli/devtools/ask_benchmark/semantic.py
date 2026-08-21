from __future__ import annotations

import csv
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from features.schema.annotate_service import AnnotateService
from features.schema.semantic_layer.ai_annotator import (
    SAMPLE_ROWS_PER_PROMPT,
    AIAnnotator,
)
from features.schema.semantic_layer.introspector import SchemaIntrospector
from features.schema.semantic_layer.manager import SemanticLayerManager
from features.schema.semantic_models import (
    ColumnAnnotation,
    Relationship,
    SemanticLayer,
    TableAnnotation,
)
from shared.persistence import write_json, write_text

from .bird_dataset import DATASET_REVISION
from .executor import MySQLConnectionConfig
from .models import ContextMode
from .provision import DESCRIPTION_PREFIX, TABLES_ENTRY, ProvisioningError

FROZEN_LLM_ENRICHED_SNAPSHOT_ID = "sonnet46-llm-enriched-v6"
FROZEN_LLM_ENRICHED_DIR = (
    Path(__file__).with_name("frozen_schemas") / FROZEN_LLM_ENRICHED_SNAPSHOT_ID
)


def semantic_dir_for_context(cache_dir: Path, context_mode: ContextMode) -> Path:
    if context_mode == ContextMode.AUTO_INIT:
        return cache_dir / "semantic-auto-init"
    if context_mode == ContextMode.AUTO_INIT_PROFILED_VALUES:
        return cache_dir / "semantic-auto-init-profiled-values"
    if context_mode == ContextMode.LLM_ENRICHED:
        return cache_dir / "semantic-llm-enriched"
    return cache_dir / "semantic"


def install_frozen_llm_enriched_layers(
    db_ids: list[str],
    source_dir: Path,
    output_dir: Path,
    *,
    force: bool = False,
) -> list[Path]:
    """Install and verify the checked-in Sonnet schema annotations."""
    provenance_path = FROZEN_LLM_ENRICHED_DIR / "provenance.json"
    try:
        provenance_text = provenance_path.read_text(encoding="utf-8")
        provenance = json.loads(provenance_text)
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvisioningError(
            f"Frozen LLM-enriched provenance is invalid at {provenance_path}: {exc}"
        ) from exc

    required = {
        "snapshot_id": FROZEN_LLM_ENRICHED_SNAPSHOT_ID,
        "dataset_revision": DATASET_REVISION,
        "source_context": ContextMode.AUTO_INIT.value,
        "output_context": ContextMode.LLM_ENRICHED.value,
    }
    for field, expected in required.items():
        if provenance.get(field) != expected:
            raise ProvisioningError(
                f"Frozen LLM-enriched provenance has {field}="
                f"{provenance.get(field)!r}; expected {expected!r}"
            )
    model = provenance.get("model")
    if not isinstance(model, dict) or model.get("model") != "claude-sonnet-4-6":
        raise ProvisioningError(
            "Frozen LLM-enriched provenance does not identify claude-sonnet-4-6"
        )
    content_audit = provenance.get("content_audit")
    forbidden_flags = (
        "contains_raw_sample_rows",
        "contains_questions",
        "contains_evidence",
        "contains_gold_sql",
        "contains_bird_curated_descriptions",
        "contains_call_receipts",
        "contains_credentials",
    )
    if not isinstance(content_audit, dict) or any(
        content_audit.get(field) is not False for field in forbidden_flags
    ):
        raise ProvisioningError("Frozen LLM-enriched content audit is incomplete")

    expected_ids = set(db_ids)
    source_content_hashes = provenance.get("source_content_hashes")
    output_content_hashes = provenance.get("output_content_hashes")
    if not isinstance(source_content_hashes, dict) or set(source_content_hashes) != (
        expected_ids
    ):
        raise ProvisioningError(
            "Frozen LLM-enriched source hashes do not match the requested databases"
        )
    if (
        not isinstance(output_content_hashes, dict)
        or set(output_content_hashes) != expected_ids
    ):
        raise ProvisioningError(
            "Frozen LLM-enriched output hashes do not match the requested databases"
        )

    annotations_path = FROZEN_LLM_ENRICHED_DIR / "annotations.json"
    try:
        annotation_bytes = annotations_path.read_bytes()
        annotations = json.loads(annotation_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvisioningError(
            f"Frozen LLM-enriched annotations are invalid at {annotations_path}: {exc}"
        ) from exc
    if hashlib.sha256(annotation_bytes).hexdigest() != provenance.get(
        "annotations_sha256"
    ):
        raise ProvisioningError("Frozen LLM-enriched annotation hash differs")
    if annotations.get("snapshot_id") != FROZEN_LLM_ENRICHED_SNAPSHOT_ID:
        raise ProvisioningError("Frozen LLM-enriched annotation identity differs")
    database_annotations = annotations.get("databases")
    if not isinstance(database_annotations, dict) or set(database_annotations) != (
        expected_ids
    ):
        raise ProvisioningError(
            "Frozen LLM-enriched annotations do not match the requested databases"
        )

    source_manager = SemanticLayerManager(base_dir=source_dir)
    output_manager = SemanticLayerManager(base_dir=output_dir)
    enriched_layers: dict[str, SemanticLayer] = {}
    table_count = 0
    column_count = 0
    for db_id in sorted(expected_ids):
        source_path = source_dir / f"{db_id}.yaml"
        if semantic_content_hash(source_path) != source_content_hashes[db_id]:
            raise ProvisioningError(
                f"Auto-init schema content differs from frozen annotations: {db_id}"
            )
        layer = deepcopy(source_manager.load(db_id, use_cache=False))
        database_delta = database_annotations[db_id]
        table_deltas = (
            database_delta.get("tables") if isinstance(database_delta, dict) else None
        )
        if not isinstance(table_deltas, dict) or set(table_deltas) != set(layer.tables):
            raise ProvisioningError(
                f"Frozen LLM-enriched table annotations differ for {db_id}"
            )
        for table_name, table in layer.tables.items():
            table_delta = table_deltas[table_name]
            if not isinstance(table_delta, dict):
                raise ProvisioningError(
                    f"Frozen table annotation is invalid: {db_id}.{table_name}"
                )
            column_deltas = table_delta.get("columns")
            if not isinstance(column_deltas, dict) or set(column_deltas) != set(
                table.columns
            ):
                raise ProvisioningError(
                    f"Frozen column annotations differ for {db_id}.{table_name}"
                )
            description = table_delta.get("description")
            business_context = table_delta.get("business_context")
            if not isinstance(description, str) or not isinstance(
                business_context, str
            ):
                raise ProvisioningError(
                    f"Frozen table text is invalid: {db_id}.{table_name}"
                )
            table.description = description
            table.business_context = business_context
            for column_name, column in table.columns.items():
                column_description = column_deltas[column_name]
                if not isinstance(column_description, str):
                    raise ProvisioningError(
                        "Frozen column text is invalid: "
                        f"{db_id}.{table_name}.{column_name}"
                    )
                column.description = column_description
            table_count += 1
            column_count += len(table.columns)
        if semantic_layer_content_hash(layer) != output_content_hashes[db_id]:
            raise ProvisioningError(
                f"Reconstructed LLM-enriched schema hash differs for {db_id}"
            )
        enriched_layers[db_id] = layer
    if table_count != provenance.get("annotated_tables") or column_count != (
        provenance.get("annotated_columns")
    ):
        raise ProvisioningError(
            "Frozen LLM-enriched structural counts differ from provenance"
        )

    installed = []
    for db_id, layer in enriched_layers.items():
        target = output_manager.get_path(db_id)
        expected_digest = output_content_hashes[db_id]
        current_digest = semantic_content_hash(target) if target.exists() else None
        if current_digest != expected_digest:
            if current_digest is not None and not force:
                raise ProvisioningError(
                    f"Installed LLM-enriched schema differs for {db_id}; use --force"
                )
            output_manager.save(layer)
            if semantic_content_hash(target) != expected_digest:
                raise ProvisioningError(
                    f"Installed LLM-enriched schema hash differs for {db_id}"
                )
        installed.append(target)

    installed_provenance = output_dir / "provenance.json"
    if installed_provenance.exists() and (
        installed_provenance.read_text(encoding="utf-8") != provenance_text
        and not force
    ):
        raise ProvisioningError(
            "Installed LLM-enriched provenance differs; use --force"
        )
    if (
        not installed_provenance.exists()
        or installed_provenance.read_text(encoding="utf-8") != provenance_text
    ):
        write_text(installed_provenance, provenance_text)
    return installed


def semantic_content_hash(path: Path) -> str:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProvisioningError(
            f"Unable to read semantic schema {path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise ProvisioningError(f"Semantic schema must be an object: {path}")
    return _semantic_payload_hash(payload)


def semantic_layer_content_hash(layer: SemanticLayer) -> str:
    return _semantic_payload_hash(layer.to_dict())


def _semantic_payload_hash(payload: dict[str, Any]) -> str:
    payload = dict(payload)
    payload.pop("created_at", None)
    payload.pop("updated_at", None)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_auto_init_layers(
    db_ids: list[str],
    output_dir: Path,
    connection: MySQLConnectionConfig,
    *,
    enum_threshold: int = 20,
    sample_enums: bool = True,
    force: bool = False,
) -> list[Path]:
    """Materialize actual schema-init output for isolated BIRD databases."""
    manager = SemanticLayerManager(base_dir=output_dir)
    paths = []
    for db_id in sorted(set(db_ids)):
        path = manager.get_path(db_id)
        if path.exists() and not force:
            paths.append(path)
            continue
        target_config = {
            "engine": "mysql",
            "host": connection.host,
            "port": connection.port,
            "user": connection.user_for(db_id),
            "password": connection.password,
            "database": connection.database_for(db_id),
            "unix_socket": connection.unix_socket,
        }
        layer = SchemaIntrospector(target_config).introspect(
            target_name=db_id,
            enum_threshold=enum_threshold,
            sample_enums=sample_enums,
        )
        if not layer.tables or any(
            not table.columns for table in layer.tables.values()
        ):
            raise ProvisioningError(
                f"Auto-init structural floor failed for database {db_id}"
            )
        manager.save(layer)
        paths.append(path)
    return paths


def build_llm_enriched_layers(
    db_ids: list[str],
    source_dir: Path,
    output_dir: Path,
    connection: MySQLConnectionConfig,
    annotator: AIAnnotator,
    *,
    sample_rows: int = 5,
    force: bool = False,
) -> tuple[list[Path], dict[str, Any]]:
    """Freeze RDST AI annotations derived only from auto-init schema and samples."""
    source_manager = SemanticLayerManager(base_dir=source_dir)
    output_manager = SemanticLayerManager(base_dir=output_dir)
    service = AnnotateService()
    paths = []
    annotated_tables = 0
    annotated_columns = 0
    sample_inputs: dict[str, dict[str, dict[str, Any]]] = {}
    total_sample_rows = 0
    for db_id in sorted(set(db_ids)):
        if not source_manager.exists(db_id):
            raise ProvisioningError(f"Auto-init schema is missing for database {db_id}")
        output_path = output_manager.get_path(db_id)
        if force or not output_path.exists():
            layer = deepcopy(source_manager.load(db_id, use_cache=False))
            output_manager.save(layer)
        layer = output_manager.load(db_id, use_cache=False)
        target_config = {
            "engine": "mysql",
            "host": connection.host,
            "port": connection.port,
            "user": connection.user_for(db_id),
            "password": connection.password,
            "database": connection.database_for(db_id),
            "unix_socket": connection.unix_socket,
        }
        order_columns_by_table, hash_order_tables = service._sample_order_plan(layer)
        sample_data_fn = service._create_sample_data_function(
            db_id,
            target_config,
            sample_rows,
            order_columns_by_table=order_columns_by_table,
            hash_order_tables=hash_order_tables,
            raise_sample_errors=True,
        )
        sample_data_by_table = {
            table_name: sample_data_fn(table_name) if sample_data_fn else []
            for table_name in sorted(layer.tables)
        }
        sample_inputs[db_id] = {}
        for table_name, rows in sample_data_by_table.items():
            total_sample_rows += len(rows)
            canonical_rows = json.dumps(
                rows,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                default=str,
            ).encode()
            sample_inputs[db_id][table_name] = {
                "rows_fetched": len(rows),
                "rows_in_prompt": min(len(rows), SAMPLE_ROWS_PER_PROMPT),
                "rows_sha256": hashlib.sha256(canonical_rows).hexdigest(),
                "order_by_columns": order_columns_by_table[table_name],
                "order_strategy": (
                    "row-sha256" if table_name in hash_order_tables else "primary-key"
                ),
            }
        work = [
            (table_name, layer.tables[table_name])
            for table_name in sorted(layer.tables)
            if service._table_needs_annotation(layer.tables[table_name])
        ]
        for start in range(0, len(work), service.CONCURRENT_TABLES):
            batch = work[start : start + service.CONCURRENT_TABLES]

            def annotate_one(
                item,
                db_id=db_id,
                sample_data_by_table=sample_data_by_table,
            ):
                table_name, table = item
                pending = [
                    name
                    for name, column in table.columns.items()
                    if column.needs_annotation
                ]
                sample_data = sample_data_by_table[table_name]
                result = annotator.annotate_table(
                    table_name,
                    table,
                    sample_data,
                    f"{db_id} database",
                    only_columns=pending,
                )
                return table, result

            with ThreadPoolExecutor(max_workers=service.CONCURRENT_TABLES) as executor:
                outcomes = list(executor.map(annotate_one, batch))
            batch_changed = False
            for table, result in outcomes:
                tables_added, columns_added, changed = service._apply_result(
                    table, result
                )
                annotated_tables += tables_added
                annotated_columns += columns_added
                batch_changed = batch_changed or changed
            if batch_changed:
                output_manager.save(layer)
        incomplete = [
            table_name
            for table_name, table in layer.tables.items()
            if service._table_needs_annotation(table)
        ]
        if incomplete:
            raise ProvisioningError(
                f"AI annotation remains incomplete for {db_id}: {', '.join(incomplete)}"
            )
        paths.append(output_path)
    return paths, {
        "annotated_tables": annotated_tables,
        "annotated_columns": annotated_columns,
        "sample_rows": sample_rows,
        "sample_rows_requested_per_table": sample_rows,
        "sample_rows_fetched_total": total_sample_rows,
        "sample_order_policy": "primary-key-else-row-sha256-v2",
        "sample_inputs": sample_inputs,
        "annotation_temperature": 0.0,
    }


def build_semantic_layers(extracted_dir: Path, output_dir: Path) -> list[Path]:
    tables_path = extracted_dir.joinpath(*PurePosixPath(TABLES_ENTRY).parts)
    try:
        databases = json.loads(tables_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProvisioningError(f"Unable to load BIRD schema metadata: {exc}") from exc

    manager = SemanticLayerManager(base_dir=output_dir)
    paths = []
    for database in databases:
        layer = _build_layer(database, extracted_dir)
        manager.save(layer)
        paths.append(manager.get_path(layer.target))
    source_paths = [tables_path, *_description_paths(extracted_dir)]
    write_json(
        output_dir / "provenance.json",
        {
            "schema_version": 1,
            "dataset_revision": DATASET_REVISION,
            "source_archive_sha256": extracted_dir.name,
            "source_context": "bird-mini-dev-metadata",
            "output_context": ContextMode.BIRD_CURATED.value,
            "construction_policy": ("dev-tables-plus-database-description-csv-v1"),
            "contains_questions": False,
            "contains_evidence": False,
            "contains_gold_sql": False,
            "source_hashes": {
                path.relative_to(extracted_dir).as_posix(): _file_sha256(path)
                for path in source_paths
            },
            "output_hashes": {path.stem: _file_sha256(path) for path in sorted(paths)},
        },
    )
    return paths


def _description_paths(extracted_dir: Path) -> list[Path]:
    root = extracted_dir.joinpath(*PurePosixPath(DESCRIPTION_PREFIX).parts)
    return sorted(root.glob("*/database_description/*.csv"))


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_layer(database: dict, extracted_dir: Path) -> SemanticLayer:
    db_id = str(database["db_id"])
    table_names = [str(name) for name in database["table_names_original"]]
    descriptions = _load_descriptions(extracted_dir, db_id)
    layer = SemanticLayer(target=db_id)
    for table_name in table_names:
        layer.tables[table_name] = TableAnnotation(name=table_name)

    column_names = database["column_names_original"]
    column_types = database["column_types"]
    for (table_index, column_name), data_type in zip(column_names, column_types):
        if table_index < 0 or column_name == "*":
            continue
        table_name = table_names[table_index]
        description = descriptions.get(table_name.casefold(), {}).get(
            str(column_name).casefold(), ""
        )
        layer.tables[table_name].columns[str(column_name)] = ColumnAnnotation(
            name=str(column_name),
            description=description,
            data_type=str(data_type),
        )

    for source_index, target_index in database.get("foreign_keys", []):
        source_table_index, source_column = column_names[source_index]
        target_table_index, target_column = column_names[target_index]
        source_table = table_names[source_table_index]
        target_table = table_names[target_table_index]
        layer.tables[source_table].relationships.append(
            Relationship(
                target_table=target_table,
                join_pattern=(
                    f"{source_table}.{source_column} = {target_table}.{target_column}"
                ),
                relationship_type="many_to_one",
            )
        )
    return layer


def _load_descriptions(extracted_dir: Path, db_id: str):
    root = extracted_dir.joinpath(
        *PurePosixPath(f"{DESCRIPTION_PREFIX}{db_id}/database_description").parts
    )
    descriptions: dict[str, dict[str, str]] = {}
    for path in sorted(root.glob("*.csv"), key=lambda item: item.name.casefold()):
        text = _decode_csv(path)
        rows = csv.DictReader(io.StringIO(text, newline=""))
        table = descriptions.setdefault(path.stem.casefold(), {})
        for row in rows:
            original_name = (row.get("original_column_name") or "").strip()
            if not original_name:
                continue
            parts = []
            for key, label in (
                ("column_description", ""),
                ("data_format", "Format"),
                ("value_description", "Values"),
            ):
                value = (row.get(key) or "").strip()
                if value:
                    parts.append(f"{label}: {value}" if label else value)
            table[original_name.casefold()] = "; ".join(parts)
    return descriptions


def _decode_csv(path: Path) -> str:
    content = path.read_bytes()
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp1252")

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, replace
from functools import cmp_to_key
from itertools import combinations
from pathlib import Path
from time import perf_counter
from typing import Any

import pymysql

from features.schema.semantic_layer.manager import SemanticLayerManager
from features.schema.semantic_models import SemanticLayer
from shared.db_connection import quote_identifier
from shared.persistence import write_json, write_text

from .bird_dataset import DATASET_REVISION
from .executor import MySQLConnectionConfig
from .semantic import semantic_content_hash

VALUE_PROFILE_FORMAT_VERSION = "rdst-exact-value-profile-v1"
VALUE_PROFILE_CONTEXT_VERSION = "rdst-question-matched-values-v3"
VALUE_GROUNDING_CONTEXT_VERSION = "rdst-question-matched-values-v4-ranked-locations"
MAX_DISTINCT_VALUES_PER_COLUMN = 10_000
MAX_INDEXED_VALUE_CHARS = 160
MAX_MATCHED_VALUES = 12
MAX_COLUMNS_PER_VALUE = 8
MAX_DECLARED_JOIN_PATHS = 6
MAX_DECLARED_JOIN_EDGES = 3
INDEXED_DATA_TYPES = frozenset({"text", "varchar", "enum"})
_VALUE_LIKE_DATA_TYPES = INDEXED_DATA_TYPES | frozenset({"char", "json"})

_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)
_SPACE = re.compile(r"\s+")
_CONNECTORS = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "by",
        "for",
        "from",
        "in",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)
_IGNORED_SINGLE_TOKEN_VALUES = _CONNECTORS | frozenset(
    {
        "all",
        "average",
        "code",
        "count",
        "full",
        "how",
        "list",
        "most",
        "name",
        "names",
        "number",
        "state",
        "total",
        "what",
        "which",
        "who",
    }
)


@dataclass(frozen=True)
class ValueOccurrence:
    table: str
    column: str
    value: str
    count: int
    rank: int = 0
    question_overlap: int = 0


@dataclass(frozen=True)
class DeclaredJoinPath:
    tables: tuple[str, ...]
    joins: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"tables": list(self.tables), "joins": list(self.joins)}


@dataclass(frozen=True)
class SuppressedSchemaMatch:
    value: str
    normalized_value: str
    schema_locations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MatchedValue:
    value: str
    normalized_value: str
    match_kind: str
    occurrences: tuple[ValueOccurrence, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "normalized_value": self.normalized_value,
            "match_kind": self.match_kind,
            "occurrences": [asdict(item) for item in self.occurrences],
        }


@dataclass(frozen=True)
class ValueMatchResult:
    context_version: str
    target: str
    profile_sha256: str
    matches: tuple[MatchedValue, ...]
    declared_join_paths: tuple[DeclaredJoinPath, ...] = ()
    suppressed_schema_matches: tuple[SuppressedSchemaMatch, ...] = ()

    @property
    def context(self) -> str:
        if not self.matches:
            return ""
        lines = [
            (
                "These exact values occur in the database and match phrases in the "
                "question. Locations are ranked by question/schema overlap. Equivalent "
                "fields use stored value support only as a tie-breaker. They are "
                "grounding hints, not extra user requirements."
            )
        ]
        for match in self.matches:
            locations = "; ".join(
                f"{item.rank}. {item.table}.{item.column}" for item in match.occurrences
            )
            lines.append(f"- {match.value!r}: {locations}")
        if self.declared_join_paths:
            lines.append(
                "Declared join paths among candidate tables. Use a path only when the "
                "question requires both sides:"
            )
            for path in self.declared_join_paths:
                lines.append(f"- {'; '.join(path.joins)}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        context = self.context
        return {
            "context_version": self.context_version,
            "target": self.target,
            "profile_sha256": self.profile_sha256,
            "context_chars": len(context),
            "context_sha256": (
                hashlib.sha256(context.encode()).hexdigest() if context else None
            ),
            "matches": [match.to_dict() for match in self.matches],
            "declared_join_paths": [
                path.to_dict() for path in self.declared_join_paths
            ],
            "suppressed_schema_matches": [
                match.to_dict() for match in self.suppressed_schema_matches
            ],
        }


class ExactValueProfile:
    """Question-time exact matching over one frozen local value profile."""

    def __init__(self, path: Path, semantic_layer: SemanticLayer | None = None):
        raw = path.read_bytes()
        payload = json.loads(raw)
        if payload.get("format_version") != VALUE_PROFILE_FORMAT_VERSION:
            raise ValueError(f"Unsupported value profile format at {path}")
        target = payload.get("target")
        columns = payload.get("columns")
        if not isinstance(target, str) or not isinstance(columns, list):
            raise TypeError(f"Invalid value profile at {path}")
        self.target = target
        self.sha256 = hashlib.sha256(raw).hexdigest()
        self._semantic_layer = semantic_layer
        self._non_value_schema_phrases = _non_value_schema_phrases(semantic_layer)
        self._join_graph = _declared_join_graph(semantic_layer)
        exact: dict[str, list[ValueOccurrence]] = defaultdict(list)
        connector_normalized: dict[str, list[ValueOccurrence]] = defaultdict(list)
        for column in columns:
            if not isinstance(column, dict):
                raise TypeError(f"Invalid value-profile column at {path}")
            table = column.get("table")
            column_name = column.get("column")
            values = column.get("values")
            if not isinstance(table, str) or not isinstance(column_name, str):
                raise TypeError(f"Invalid value-profile identity at {path}")
            if not isinstance(values, list):
                raise TypeError(f"Invalid value-profile values at {path}")
            for record in values:
                value = record.get("value") if isinstance(record, dict) else None
                count = record.get("count") if isinstance(record, dict) else None
                if not isinstance(value, str) or not isinstance(count, int):
                    raise TypeError(f"Invalid value-profile record at {path}")
                normalized = _normalize(value)
                if not _indexable_normalized_value(normalized):
                    continue
                occurrence = ValueOccurrence(table, column_name, value, count)
                exact[normalized].append(occurrence)
                without_connectors = _without_connectors(normalized)
                if without_connectors:
                    connector_normalized[without_connectors].append(occurrence)
        self._exact = exact
        self._connector_normalized = connector_normalized

    def match(self, question: str) -> ValueMatchResult:
        normalized_question = _normalize(question)
        allowed_singletons = _explicit_singleton_candidates(question)
        exact_candidates = _question_candidates(
            normalized_question, allowed_singletons=allowed_singletons
        )
        connector_candidates = _question_candidates(
            _without_connectors(normalized_question),
            allowed_singletons=allowed_singletons,
        )
        grouped: dict[str, tuple[str, list[ValueOccurrence]]] = {}
        suppressed: dict[str, SuppressedSchemaMatch] = {}
        for candidate in exact_candidates:
            occurrences = self._exact.get(candidate)
            if occurrences:
                schema_locations = self._non_value_schema_phrases.get(candidate, ())
                if schema_locations:
                    suppressed[candidate] = SuppressedSchemaMatch(
                        value=min(
                            (item.value for item in occurrences),
                            key=lambda value: (len(value), value),
                        ),
                        normalized_value=candidate,
                        schema_locations=schema_locations,
                    )
                    continue
                grouped[candidate] = ("exact", list(occurrences))
        for candidate in connector_candidates:
            occurrences = self._connector_normalized.get(candidate)
            if not occurrences or candidate in grouped:
                continue
            schema_locations = self._non_value_schema_phrases.get(candidate, ())
            if schema_locations:
                suppressed[candidate] = SuppressedSchemaMatch(
                    value=min(
                        (item.value for item in occurrences),
                        key=lambda value: (len(value), value),
                    ),
                    normalized_value=candidate,
                    schema_locations=schema_locations,
                )
                continue
            grouped[candidate] = ("connector-normalized", list(occurrences))

        ranked = sorted(
            grouped.items(),
            key=lambda item: (
                -len(item[0].split()),
                -len(item[0]),
                min(occurrence.count for occurrence in item[1][1]),
                item[0],
            ),
        )
        matches = []
        seen_occurrences: set[tuple[tuple[str, str, str, int], ...]] = set()
        for normalized, (match_kind, occurrences) in ranked:
            deduplicated = _rank_occurrences(
                occurrences,
                normalized_question,
            )[:MAX_COLUMNS_PER_VALUE]
            occurrence_key = tuple(
                (item.table, item.column, item.value, item.count)
                for item in deduplicated
            )
            if occurrence_key in seen_occurrences:
                continue
            seen_occurrences.add(occurrence_key)
            display_value = min(
                (item.value for item in deduplicated),
                key=lambda value: (len(value), value),
            )
            matches.append(
                MatchedValue(
                    value=display_value,
                    normalized_value=normalized,
                    match_kind=match_kind,
                    occurrences=tuple(deduplicated),
                )
            )
            if len(matches) >= MAX_MATCHED_VALUES:
                break
        return ValueMatchResult(
            context_version=VALUE_GROUNDING_CONTEXT_VERSION,
            target=self.target,
            profile_sha256=self.sha256,
            matches=tuple(matches),
            declared_join_paths=_join_paths_for_matches(matches, self._join_graph),
            suppressed_schema_matches=tuple(
                sorted(
                    suppressed.values(),
                    key=lambda item: (item.normalized_value, item.schema_locations),
                )
            ),
        )


class ExactValueProfileStore:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self._cache: dict[str, ExactValueProfile] = {}
        self._semantic_manager = SemanticLayerManager(base_dir=self.base_dir)

    def load(self, target: str) -> ExactValueProfile:
        if target not in self._cache:
            path = self.base_dir / f"{target}.values.json"
            if not path.is_file():
                raise FileNotFoundError(f"Exact value profile is missing: {path}")
            layer = self._semantic_manager.load(target, use_cache=False)
            profile = ExactValueProfile(path, semantic_layer=layer)
            if profile.target != target:
                raise ValueError(
                    "Exact value profile target is "
                    f"{profile.target!r}, expected {target!r}"
                )
            self._cache[target] = profile
        return self._cache[target]

    def match(self, target: str, question: str) -> ValueMatchResult:
        return self.load(target).match(question)


def _rank_occurrences(
    occurrences: list[ValueOccurrence], normalized_question: str
) -> list[ValueOccurrence]:
    question_terms = {_term_root(token) for token in normalized_question.split()}
    deduplicated = {
        (item.table, item.column, item.value, item.count): item for item in occurrences
    }.values()
    scored = []
    for occurrence in deduplicated:
        column_terms = set(_identifier_terms(occurrence.column))
        table_terms = set(_identifier_terms(occurrence.table))
        overlap = 2 * len(column_terms & question_terms) + len(
            table_terms & question_terms
        )
        scored.append((occurrence, overlap))
    scored.sort(key=cmp_to_key(_compare_ranked_occurrences))
    return [
        replace(occurrence, rank=index, question_overlap=overlap)
        for index, (occurrence, overlap) in enumerate(scored, start=1)
    ]


def _compare_ranked_occurrences(
    first: tuple[ValueOccurrence, int], second: tuple[ValueOccurrence, int]
) -> int:
    first_occurrence, first_overlap = first
    second_occurrence, second_overlap = second
    if first_overlap != second_overlap:
        return -1 if first_overlap > second_overlap else 1
    equivalent_fields = (
        first_occurrence.table.casefold() == second_occurrence.table.casefold()
        and _identifier_terms(first_occurrence.column)
        == _identifier_terms(second_occurrence.column)
    )
    if equivalent_fields and first_occurrence.count != second_occurrence.count:
        return -1 if first_occurrence.count > second_occurrence.count else 1
    first_identity = (
        first_occurrence.table.casefold(),
        first_occurrence.column.casefold(),
        first_occurrence.value,
    )
    second_identity = (
        second_occurrence.table.casefold(),
        second_occurrence.column.casefold(),
        second_occurrence.value,
    )
    return (first_identity > second_identity) - (first_identity < second_identity)


def _identifier_terms(identifier: str) -> tuple[str, ...]:
    separated = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", identifier)
    separated = separated.replace("_", " ")
    return tuple(_term_root(token) for token in _normalize(separated).split())


def _term_root(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _non_value_schema_phrases(
    layer: SemanticLayer | None,
) -> dict[str, tuple[str, ...]]:
    if layer is None:
        return {}
    locations: dict[str, set[str]] = defaultdict(set)
    for table_name, table in layer.tables.items():
        for column_name, column in table.columns.items():
            data_type = column.data_type.casefold().split("(", 1)[0]
            if data_type in _VALUE_LIKE_DATA_TYPES:
                continue
            tokens = _normalize(column_name).split()
            for start in range(len(tokens)):
                for size in range(1, min(8, len(tokens) - start) + 1):
                    phrase_tokens = tokens[start : start + size]
                    if size < 2 and not any(token.isdigit() for token in phrase_tokens):
                        continue
                    locations[" ".join(phrase_tokens)].add(
                        f"{table_name}.{column_name}"
                    )
    return {
        phrase: tuple(sorted(schema_locations))
        for phrase, schema_locations in locations.items()
    }


def _declared_join_graph(
    layer: SemanticLayer | None,
) -> dict[str, tuple[tuple[str, str], ...]]:
    if layer is None:
        return {}
    graph: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for source_table, table in layer.tables.items():
        for relationship in table.relationships:
            target_table = relationship.target_table
            join = relationship.join_pattern.strip()
            if not target_table or not join or target_table not in layer.tables:
                continue
            graph[source_table].add((target_table, join))
            graph[target_table].add((source_table, join))
    return {
        table: tuple(sorted(edges, key=lambda edge: (edge[0], edge[1])))
        for table, edges in graph.items()
    }


def _join_paths_for_matches(
    matches: list[MatchedValue],
    graph: dict[str, tuple[tuple[str, str], ...]],
) -> tuple[DeclaredJoinPath, ...]:
    if not graph:
        return ()
    candidate_tables = sorted(
        {occurrence.table for match in matches for occurrence in match.occurrences}
    )
    paths = []
    seen_joins: set[tuple[str, ...]] = set()
    for source, target in combinations(candidate_tables, 2):
        path = _shortest_declared_join_path(source, target, graph)
        if path is None or path.joins in seen_joins:
            continue
        seen_joins.add(path.joins)
        paths.append(path)
    paths.sort(key=lambda path: (len(path.joins), path.tables, path.joins))
    return tuple(paths[:MAX_DECLARED_JOIN_PATHS])


def _shortest_declared_join_path(
    source: str,
    target: str,
    graph: dict[str, tuple[tuple[str, str], ...]],
) -> DeclaredJoinPath | None:
    queue = deque([(source, (source,), ())])
    visited = {source}
    while queue:
        table, tables, joins = queue.popleft()
        if len(joins) >= MAX_DECLARED_JOIN_EDGES:
            continue
        for next_table, join in graph.get(table, ()):
            if next_table in visited:
                continue
            next_tables = (*tables, next_table)
            next_joins = (*joins, join)
            if next_table == target:
                return DeclaredJoinPath(tables=next_tables, joins=next_joins)
            visited.add(next_table)
            queue.append((next_table, next_tables, next_joins))
    return None


def build_exact_value_profiles(
    db_ids: list[str],
    source_dir: Path,
    output_dir: Path,
    connection: MySQLConnectionConfig,
    *,
    force: bool = False,
    max_values_per_column: int = MAX_DISTINCT_VALUES_PER_COLUMN,
    max_value_chars: int = MAX_INDEXED_VALUE_CHARS,
) -> dict[str, Any]:
    """Build local exact-value profiles from frozen auto-init schemas."""
    if max_values_per_column <= 0 or max_value_chars <= 0:
        raise ValueError("Value profile limits must be positive")
    source_manager = SemanticLayerManager(base_dir=source_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    requested_db_ids = sorted(set(db_ids))
    if not force:
        frozen = _load_frozen_provenance(
            requested_db_ids,
            source_dir,
            output_dir,
            max_values_per_column=max_values_per_column,
            max_value_chars=max_value_chars,
        )
        if frozen is not None:
            return {**frozen, "_reused": True}
    source_hashes: dict[str, str] = {}
    schema_hashes: dict[str, str] = {}
    profile_hashes: dict[str, str] = {}
    database_stats: dict[str, dict[str, Any]] = {}
    started = perf_counter()

    for db_id in requested_db_ids:
        source_path = source_dir / f"{db_id}.yaml"
        if not source_path.is_file() or not source_manager.exists(db_id):
            raise FileNotFoundError(f"Auto-init schema is missing: {source_path}")
        source_hash = semantic_content_hash(source_path)
        source_hashes[db_id] = source_hash
        output_schema = output_dir / source_path.name
        if force or not output_schema.exists():
            write_text(output_schema, source_path.read_text(encoding="utf-8"))
        if semantic_content_hash(output_schema) != source_hash:
            raise ValueError(f"Profiled context schema differs for {db_id}")
        schema_hashes[db_id] = source_hash

        profile_path = output_dir / f"{db_id}.values.json"
        if profile_path.exists() and not force:
            profile = ExactValueProfile(profile_path)
            if profile.target != db_id:
                raise ValueError(
                    f"Existing value profile has wrong target: {profile_path}"
                )
            payload = json.loads(profile_path.read_bytes())
            if payload.get("source_schema_sha256") != source_hash:
                raise ValueError(
                    "Existing value profile has stale schema provenance: "
                    f"{profile_path}"
                )
            profile_hashes[db_id] = profile.sha256
            database_stats[db_id] = payload.get("stats", {})
            continue

        layer = source_manager.load(db_id, use_cache=False)
        payload = _build_database_profile(
            db_id,
            layer,
            connection,
            source_schema_sha256=source_hash,
            max_values_per_column=max_values_per_column,
            max_value_chars=max_value_chars,
        )
        write_json(profile_path, payload)
        raw = profile_path.read_bytes()
        profile_hashes[db_id] = hashlib.sha256(raw).hexdigest()
        database_stats[db_id] = payload["stats"]

    provenance = {
        "format_version": VALUE_PROFILE_FORMAT_VERSION,
        "context_version": VALUE_PROFILE_CONTEXT_VERSION,
        "dataset_revision": DATASET_REVISION,
        "source_context": "auto-init",
        "output_context": "auto-init-profiled-values",
        "construction_policy": "local-exact-distinct-values-v1",
        "max_values_per_column": max_values_per_column,
        "max_value_chars": max_value_chars,
        "indexed_data_types": sorted(INDEXED_DATA_TYPES),
        "contains_questions": False,
        "contains_evidence": False,
        "contains_gold_sql": False,
        "contains_bird_curated_descriptions": False,
        "contains_database_values": True,
        "source_schema_hashes": source_hashes,
        "output_schema_hashes": schema_hashes,
        "value_profile_hashes": profile_hashes,
        "database_stats": database_stats,
        "wall_time_seconds": perf_counter() - started,
    }
    write_json(output_dir / "provenance.json", provenance)
    return {**provenance, "_reused": False}


def _load_frozen_provenance(
    db_ids: list[str],
    source_dir: Path,
    output_dir: Path,
    *,
    max_values_per_column: int,
    max_value_chars: int,
) -> dict[str, Any] | None:
    path = output_dir / "provenance.json"
    if not path.exists():
        return None
    try:
        provenance = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Frozen value-profile provenance is invalid: {path}") from exc
    required = {
        "format_version": VALUE_PROFILE_FORMAT_VERSION,
        "context_version": VALUE_PROFILE_CONTEXT_VERSION,
        "dataset_revision": DATASET_REVISION,
        "source_context": "auto-init",
        "output_context": "auto-init-profiled-values",
        "construction_policy": "local-exact-distinct-values-v1",
        "max_values_per_column": max_values_per_column,
        "max_value_chars": max_value_chars,
        "indexed_data_types": sorted(INDEXED_DATA_TYPES),
        "contains_questions": False,
        "contains_evidence": False,
        "contains_gold_sql": False,
        "contains_bird_curated_descriptions": False,
        "contains_database_values": True,
    }
    for field, expected in required.items():
        if provenance.get(field) != expected:
            raise ValueError(
                f"Frozen value-profile provenance has {field}="
                f"{provenance.get(field)!r}; expected {expected!r}. "
                "Use --force to rebuild it."
            )
    hash_fields = (
        "source_schema_hashes",
        "output_schema_hashes",
        "value_profile_hashes",
        "database_stats",
    )
    mappings = {field: provenance.get(field) for field in hash_fields}
    if any(not isinstance(value, dict) for value in mappings.values()):
        raise TypeError("Frozen value-profile provenance is missing database mappings")
    expected_ids = set(db_ids)
    for field, value in mappings.items():
        if set(value) != expected_ids:
            raise ValueError(
                f"Frozen value-profile provenance has the wrong databases in {field}"
            )
    for db_id in db_ids:
        source_path = source_dir / f"{db_id}.yaml"
        output_schema_path = output_dir / f"{db_id}.yaml"
        profile_path = output_dir / f"{db_id}.values.json"
        try:
            source_hash = semantic_content_hash(source_path)
            output_hash = semantic_content_hash(output_schema_path)
            profile_hash = hashlib.sha256(profile_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise ValueError(
                f"Frozen value-profile input is unreadable for {db_id}"
            ) from exc
        if (
            mappings["source_schema_hashes"].get(db_id) != source_hash
            or mappings["output_schema_hashes"].get(db_id) != output_hash
            or mappings["value_profile_hashes"].get(db_id) != profile_hash
        ):
            raise ValueError(
                f"Frozen value-profile provenance differs for {db_id}; "
                "use --force to rebuild it"
            )
    return provenance


def _build_database_profile(
    db_id: str,
    layer: Any,
    config: MySQLConnectionConfig,
    *,
    source_schema_sha256: str,
    max_values_per_column: int,
    max_value_chars: int,
) -> dict[str, Any]:
    connection = pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user_for(db_id),
        password=config.password,
        database=config.database_for(db_id),
        unix_socket=config.unix_socket,
        connect_timeout=10,
        read_timeout=120,
        write_timeout=30,
        autocommit=False,
    )
    columns = []
    indexed_values = 0
    truncated_columns = 0
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION max_execution_time = 120000")
            cursor.execute("START TRANSACTION READ ONLY")
            for table_name in sorted(layer.tables):
                table = layer.tables[table_name]
                for column_name in sorted(table.columns):
                    column = table.columns[column_name]
                    if column.data_type.casefold() not in INDEXED_DATA_TYPES:
                        continue
                    table_sql = quote_identifier(table_name, "mysql")
                    column_sql = quote_identifier(column_name, "mysql")
                    cursor.execute(
                        f"SELECT CAST({column_sql} AS CHAR) AS value, COUNT(*) AS cnt "
                        f"FROM {table_sql} WHERE {column_sql} IS NOT NULL "
                        f"AND CHAR_LENGTH(CAST({column_sql} AS CHAR)) BETWEEN 1 AND %s "
                        f"GROUP BY {column_sql} ORDER BY cnt DESC, value ASC LIMIT %s",
                        (max_value_chars, max_values_per_column + 1),
                    )
                    rows = cursor.fetchall()
                    truncated = len(rows) > max_values_per_column
                    if truncated:
                        rows = rows[:max_values_per_column]
                        truncated_columns += 1
                    values = [
                        {"value": str(value), "count": int(count)}
                        for value, count in rows
                        if value is not None
                        and _indexable_normalized_value(_normalize(value))
                    ]
                    indexed_values += len(values)
                    columns.append(
                        {
                            "table": table_name,
                            "column": column_name,
                            "data_type": column.data_type,
                            "truncated": truncated,
                            "values": values,
                        }
                    )
            connection.rollback()
    finally:
        connection.close()

    return {
        "format_version": VALUE_PROFILE_FORMAT_VERSION,
        "target": db_id,
        "source_schema_sha256": source_schema_sha256,
        "max_values_per_column": max_values_per_column,
        "max_value_chars": max_value_chars,
        "columns": columns,
        "stats": {
            "indexed_columns": len(columns),
            "indexed_values": indexed_values,
            "truncated_columns": truncated_columns,
        },
    }


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return _SPACE.sub(" ", _NON_WORD.sub(" ", text)).strip()


def _without_connectors(value: str) -> str:
    return " ".join(token for token in value.split() if token not in _CONNECTORS)


def _indexable_normalized_value(value: str) -> bool:
    if len(value) < 3:
        return False
    tokens = value.split()
    if tokens and all(token.isdigit() for token in tokens):
        return False
    return bool(tokens) and not (
        len(tokens) == 1 and tokens[0] in _IGNORED_SINGLE_TOKEN_VALUES
    )


def _question_candidates(
    question: str,
    *,
    allowed_singletons: set[str],
    max_tokens: int = 8,
) -> set[str]:
    tokens = question.split()
    candidates = set()
    for start in range(len(tokens)):
        for size in range(1, min(max_tokens, len(tokens) - start) + 1):
            candidate = " ".join(tokens[start : start + size])
            if _indexable_normalized_value(candidate) and (
                size > 1 or candidate in allowed_singletons
            ):
                candidates.add(candidate)
    return candidates


def _explicit_singleton_candidates(question: str) -> set[str]:
    """Return quoted or proper-name-like one-token phrases from the question."""
    allowed = {
        _normalize(token)
        for quote in re.findall(r"[\"']([^\"']+)[\"']", question)
        for token in quote.split()
        if _indexable_normalized_value(_normalize(token))
    }
    tokens = re.findall(r"\b[^\W\d_]\w*\b", question, flags=re.UNICODE)
    for index, token in enumerate(tokens):
        if index == 0 or not any(character.isupper() for character in token):
            continue
        normalized = _normalize(token)
        if _indexable_normalized_value(normalized):
            allowed.add(normalized)
    return allowed

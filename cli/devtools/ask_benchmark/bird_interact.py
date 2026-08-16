from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import multiprocessing
import os
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty
from typing import Any
from uuid import uuid4

import psycopg2
import requests
import sqlglot
from sqlglot import exp

from features.ask.events import (
    AskClarificationNeededEvent,
    AskErrorEvent,
    AskResultEvent,
    AskSqlGeneratedEvent,
)
from features.ask.models import AskInput, AskOptions
from features.ask.service import AskService
from shared.db_connection import postgres_connection_kwargs
from shared.persistence import write_json

from .anthropic_adapter import AnthropicSDKAdapter
from .artifacts import ArtifactStore
from .models import ModelSpec, QueryResult
from .oracle import result_fingerprint, score_results

BIRD_INTERACT_SOURCE_REVISION = "451fe2c3518ee1cf908d8139e2913483bd519381"
BIRD_INTERACT_PUBLIC_REVISION = "10253f235dbc6092a97e9c0acd30dba6203d8e59"
BIRD_INTERACT_PUBLIC_SHA256 = (
    "32eea6778d69f4a052622a9981ee95cc5dddbff62d0d65ff6e06bd5bec757087"
)
BIRD_INTERACT_EXAMPLES_SHA256 = (
    "fa3b64e433f2974a4fe3e660bcf3954c278e4f99f8432e4d1b1add38bbee1f16"
)
BIRD_INTERACT_QUALIFICATION_CASE_IDS = ("alien_3", "alien_8")
BIRD_INTERACT_EXCLUDED_CAPABILITIES = {
    "alien_M_1": "UPDATE is outside AskService's read-only contract",
    "alien_M_2": "CREATE FUNCTION is outside AskService's read-only contract",
    "alien_M_3": "CREATE VIEW is outside AskService's read-only contract",
}
BIRD_INTERACT_PROTOCOL = "public-labeled-simulator-v4"
BIRD_INTERACT_RESCORE_PROTOCOL = "frozen-example-transcript-replay-v3"
BIRD_INTERACT_INTERACTION_MODE = "interactive-public-labeled-simulator"
BIRD_INTERACT_UNANSWERABLE_RESPONSE = (
    "Sorry, this question is out of scope, so I can not answer your question."
)
BIRD_INTERACT_POSTGRES_IMAGE = "docker.io/shawnxxh/bird-interact-postgresql:latest"
BIRD_INTERACT_PUBLIC_URL = (
    "https://huggingface.co/datasets/birdsql/mini-interact/resolve/"
    f"{BIRD_INTERACT_PUBLIC_REVISION}/mini_interact.jsonl"
)
BIRD_INTERACT_EXAMPLES_URL = (
    "https://raw.githubusercontent.com/bird-bench/BIRD-Interact/"
    f"{BIRD_INTERACT_SOURCE_REVISION}/"
    "BIRD-Interact-ADK/examples/c_interact_samples.json"
)

_SAFE_DATABASE = re.compile(r"[A-Za-z0-9_]+")
_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class FrozenExchange:
    question: str
    answer: str
    target_terms: tuple[str, ...]
    source_kind: str = "official-example-transcript"


@dataclass(frozen=True)
class BirdInteractCase:
    instance_id: str
    database: str
    ambiguous_query: str
    reference_sql: str
    target_terms: tuple[str, ...]
    exchanges: tuple[FrozenExchange, ...]
    max_clarification_turns: int
    order_sensitive: bool


@dataclass(frozen=True)
class PostgresConnectionConfig:
    host: str = "127.0.0.1"
    port: int = 5432
    user: str = "root"
    password: str = "123123"

    def database_for(self, database: str) -> str:
        if not _SAFE_DATABASE.fullmatch(database):
            raise ValueError(f"Unsafe BIRD-Interact database ID: {database!r}")
        return database


@dataclass(frozen=True)
class PostgresQueryBounds:
    timeout_seconds: int = 30
    max_rows: int = 100_000
    max_result_bytes: int = 64 * 1024 * 1024


class PostgresQualificationExecutor:
    """Process-bounded, read-only PostgreSQL executor for qualification scoring."""

    def __init__(
        self,
        config: PostgresConnectionConfig,
        bounds: PostgresQueryBounds | None = None,
    ):
        self.config = config
        self.bounds = bounds or PostgresQueryBounds()

    def execute(self, sql: str, *, db_id: str) -> QueryResult:
        started = time.perf_counter()
        methods = multiprocessing.get_all_start_methods()
        context = multiprocessing.get_context("fork" if "fork" in methods else "spawn")
        result_queue = context.Queue(maxsize=1)
        process = context.Process(
            target=_postgres_execute_worker,
            args=(self.config, self.bounds, sql, db_id, result_queue),
            daemon=True,
        )
        process.start()
        grace = min(5.0, max(0.1, self.bounds.timeout_seconds * 0.1))
        try:
            status, payload = result_queue.get(
                timeout=self.bounds.timeout_seconds + grace
            )
        except Empty:
            process.terminate()
            process.join(2)
            if process.is_alive():
                process.kill()
                process.join()
            result_queue.close()
            return QueryResult(
                execution_time_ms=(time.perf_counter() - started) * 1000,
                error="Query exceeded qualification wall-clock timeout",
                timed_out=True,
            )
        process.join(2)
        if process.is_alive():
            process.terminate()
            process.join()
        result_queue.close()
        if status == "error":
            raise RuntimeError(f"PostgreSQL qualification worker failed: {payload}")
        return payload

    def _execute_direct(self, sql: str, *, db_id: str) -> QueryResult:
        started = time.perf_counter()
        connection = None
        try:
            connection = psycopg2.connect(
                **postgres_connection_kwargs(
                    {
                        "host": self.config.host,
                        "port": self.config.port,
                        "user": self.config.user,
                        "password": self.config.password,
                        "database": self.config.database_for(db_id),
                    }
                )
            )
            connection.autocommit = False
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(
                    "SET LOCAL statement_timeout = %s",
                    (self.bounds.timeout_seconds * 1000,),
                )
                cursor.execute(sql)
                columns = tuple(
                    description[0] for description in (cursor.description or ())
                )
                rows: list[tuple[Any, ...]] = []
                result_bytes = 0
                while row := cursor.fetchone():
                    if len(rows) >= self.bounds.max_rows:
                        return QueryResult(
                            execution_time_ms=(time.perf_counter() - started) * 1000,
                            error="Query result exceeded qualification row bound",
                            result_too_large=True,
                        )
                    frozen = tuple(row)
                    result_bytes += _row_size(frozen)
                    if result_bytes > self.bounds.max_result_bytes:
                        return QueryResult(
                            execution_time_ms=(time.perf_counter() - started) * 1000,
                            error="Query result exceeded qualification byte bound",
                            result_too_large=True,
                        )
                    rows.append(frozen)
                return QueryResult(
                    columns=columns,
                    rows=tuple(rows),
                    execution_time_ms=(time.perf_counter() - started) * 1000,
                )
        except psycopg2.Error as exc:
            message = str(exc)
            return QueryResult(
                execution_time_ms=(time.perf_counter() - started) * 1000,
                error=message,
                timed_out="statement timeout" in message.casefold(),
            )
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                finally:
                    connection.close()

    def as_ask_executor(self, db_id: str):
        def execute(sql: str, _target_config: dict[str, Any]):
            result = self.execute(sql, db_id=db_id)
            error_kind = None
            if result.timed_out:
                error_kind = "timeout"
            elif result.result_too_large:
                error_kind = "result_too_large"
            return {
                "success": result.succeeded,
                "rows": [list(row) for row in result.rows],
                "columns": list(result.columns),
                "error": result.error,
                "error_kind": error_kind,
            }

        return execute


class _QualificationTargetsConfig:
    def __init__(self, target: str, config: dict[str, Any]):
        self._target = target
        self._config = config

    def load(self) -> None:
        return None

    def get_default(self):
        return self._target

    def get(self, target: str):
        return self._config if target == self._target else None


class FrozenTranscriptSimulator:
    """Replay a pinned labeled answer only after a relevant target is asked."""

    def __init__(self, case: BirdInteractCase):
        self.case = case
        self._used: set[int] = set()

    def answer(self, question: str, options: list[str]) -> dict[str, Any] | None:
        content = "\n".join([question, *options])
        mentioned = _mentioned_terms(content, self.case.target_terms)
        if not mentioned:
            return None

        candidates = []
        for index, exchange in enumerate(self.case.exchanges):
            if index in self._used:
                continue
            overlap = mentioned.intersection(exchange.target_terms)
            if overlap:
                candidates.append(
                    (
                        sum(len(_WORD.findall(term.casefold())) for term in overlap),
                        len(overlap),
                        _token_overlap(question, exchange.question),
                        -index,
                        index,
                        exchange,
                    )
                )
        if not candidates:
            return None
        _, _, overlap_score, _, index, exchange = max(candidates)
        self._used.add(index)
        return {
            "answer": exchange.answer,
            "matched_target_terms": sorted(
                mentioned.intersection(exchange.target_terms)
            ),
            "source_kind": exchange.source_kind,
            "source_exchange_index": index,
            "source_question_sha256": _sha256_text(exchange.question),
            "source_answer_sha256": _sha256_text(exchange.answer),
            "lexical_overlap": overlap_score,
        }


def default_bird_interact_cache_dir() -> Path:
    return Path.home() / ".cache" / "rdst" / "benchmarks" / "bird-interact"


def prepare_frozen_qualification_inputs(cache_dir: Path) -> tuple[Path, Path]:
    public_path = (
        cache_dir / "public" / BIRD_INTERACT_PUBLIC_REVISION / "mini_interact.jsonl"
    )
    examples_path = (
        cache_dir / "source" / BIRD_INTERACT_SOURCE_REVISION / "c_interact_samples.json"
    )
    _download_pinned_file(
        BIRD_INTERACT_PUBLIC_URL,
        public_path,
        BIRD_INTERACT_PUBLIC_SHA256,
        "BIRD-Interact public metadata",
    )
    _download_pinned_file(
        BIRD_INTERACT_EXAMPLES_URL,
        examples_path,
        BIRD_INTERACT_EXAMPLES_SHA256,
        "BIRD-Interact frozen examples",
    )
    load_frozen_qualification_cases(public_path, examples_path)
    return public_path, examples_path


def verify_postgres_provision(
    config: PostgresConnectionConfig,
    *,
    image_id: str,
    image_digest: str,
) -> dict[str, Any]:
    params = {
        "host": config.host,
        "port": config.port,
        "user": config.user,
        "password": config.password,
        "database": "postgres",
    }
    connection = psycopg2.connect(**postgres_connection_kwargs(params))
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version()")
            version = str(cursor.fetchone()[0])
            cursor.execute(
                "SELECT datname FROM pg_database "
                "WHERE datistemplate = false ORDER BY datname"
            )
            databases = [str(row[0]) for row in cursor.fetchall()]
    finally:
        connection.close()
    if "alien" not in databases:
        raise RuntimeError("BIRD-Interact PostgreSQL provision is missing alien")

    alien_params = {**params, "database": "alien"}
    connection = psycopg2.connect(**postgres_connection_kwargs(alien_params))
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
            alien_table_count = int(cursor.fetchone()[0])
    finally:
        connection.close()
    if alien_table_count <= 0:
        raise RuntimeError("BIRD-Interact alien database contains no base tables")
    return {
        "postgres_image": BIRD_INTERACT_POSTGRES_IMAGE,
        "postgres_image_id": image_id,
        "postgres_image_digest": image_digest,
        "postgres_version": version,
        "host": config.host,
        "port": config.port,
        "database_count": len(databases),
        "database_names_sha256": _sha256_text(
            json.dumps(databases, separators=(",", ":"))
        ),
        "alien_table_count": alien_table_count,
    }


def load_frozen_qualification_cases(
    public_data_path: Path,
    examples_path: Path,
    *,
    case_ids: tuple[str, ...] = BIRD_INTERACT_QUALIFICATION_CASE_IDS,
) -> list[BirdInteractCase]:
    _require_sha256(
        public_data_path,
        BIRD_INTERACT_PUBLIC_SHA256,
        "BIRD-Interact public metadata",
    )
    _require_sha256(
        examples_path,
        BIRD_INTERACT_EXAMPLES_SHA256,
        "BIRD-Interact frozen examples",
    )
    public_tasks = {}
    for line_number, line in enumerate(
        public_data_path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        task = json.loads(line)
        instance_id = str(task.get("instance_id", ""))
        if not instance_id or instance_id in public_tasks:
            raise ValueError(
                f"Invalid or duplicate instance_id at public line {line_number}"
            )
        public_tasks[instance_id] = task

    document = json.loads(examples_path.read_text(encoding="utf-8"))
    if document.get("mode") != "c-interact":
        raise ValueError("Frozen example artifact is not c-interact")
    samples = {sample["instance_id"]: sample for sample in document.get("samples", [])}
    if set(case_ids).intersection(BIRD_INTERACT_EXCLUDED_CAPABILITIES):
        raise ValueError("Qualification case IDs must stay within read-only Ask scope")

    cases = []
    for instance_id in case_ids:
        if instance_id not in public_tasks:
            raise ValueError(f"Public BIRD-Interact task is missing {instance_id}")
        if instance_id not in samples:
            raise ValueError(f"Frozen BIRD-Interact example is missing {instance_id}")
        task = public_tasks[instance_id]
        sample = samples[instance_id]
        if not sample.get("phase1_passed"):
            raise ValueError(
                f"Frozen example {instance_id} has no accepted phase-1 SQL"
            )
        if task.get("selected_database") != sample.get("database"):
            raise ValueError(f"Database mismatch for {instance_id}")

        reference_sql, exchanges = _phase_one_reference_and_exchanges(sample)
        if not _is_single_read_only_query(reference_sql):
            raise ValueError(f"Frozen example {instance_id} is not a read-only query")
        ambiguous_query = str(task.get("amb_user_query", "")).strip()
        if not ambiguous_query:
            raise ValueError(f"Public BIRD-Interact task {instance_id} has no query")
        _verify_event_query(sample, ambiguous_query)

        target_terms = _task_target_terms(task)
        frozen_exchanges = []
        for question, answer in exchanges:
            matched = tuple(sorted(_mentioned_terms(question, target_terms)))
            if not matched:
                raise ValueError(
                    f"Frozen exchange for {instance_id} has no labeled target"
                )
            frozen_exchanges.append(
                FrozenExchange(
                    question=question,
                    answer=answer,
                    target_terms=matched,
                )
            )
        frozen_exchanges.extend(
            _public_non_critical_exchanges(task, tuple(frozen_exchanges))
        )
        max_turns = (
            len(task.get("user_query_ambiguity", {}).get("critical_ambiguity", []))
            + len(task.get("knowledge_ambiguity", []))
            + 3
        )
        order_sensitive = any(
            ambiguity.get("type") == "sort_ambiguity"
            for ambiguity in task.get("user_query_ambiguity", {}).get(
                "non_critical_ambiguity", []
            )
        )
        cases.append(
            BirdInteractCase(
                instance_id=instance_id,
                database=str(task["selected_database"]),
                ambiguous_query=ambiguous_query,
                reference_sql=reference_sql,
                target_terms=target_terms,
                exchanges=tuple(frozen_exchanges),
                max_clarification_turns=max_turns,
                order_sensitive=order_sensitive,
            )
        )
    return cases


def run_frozen_qualification(
    *,
    run_id: str,
    output_dir: Path,
    cases: list[BirdInteractCase],
    model_spec: ModelSpec,
    filter_spec: ModelSpec,
    filter_aliases: tuple[str, ...],
    route_checks: dict[str, Any],
    executor: PostgresQualificationExecutor,
    database_provision: dict[str, Any],
    protocol_sha256: str,
    rdst_revision: str,
    rdst_dirty: bool,
    rdst_diff_sha256: str,
    product_max_rows: int = 100,
) -> dict[str, Any]:
    store = ArtifactStore(output_dir)
    references = {}
    reference_fingerprints = {}
    for case in cases:
        result = executor.execute(case.reference_sql, db_id=case.database)
        if not result.succeeded:
            raise RuntimeError(
                f"Reference SQL preflight failed for {case.instance_id}: {result.error}"
            )
        references[case.instance_id] = result
        reference_fingerprints[case.instance_id] = result_fingerprint(result)

    manifest = {
        "artifact_schema_version": 1,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rdst_revision": rdst_revision,
        "rdst_dirty": rdst_dirty,
        "rdst_diff_sha256": rdst_diff_sha256,
        "benchmark_protocol_sha256": protocol_sha256,
        "dataset": "bird-interact-mini-public-plus-official-examples",
        "dataset_revision": BIRD_INTERACT_PUBLIC_REVISION,
        "source_revision": BIRD_INTERACT_SOURCE_REVISION,
        "public_data_sha256": BIRD_INTERACT_PUBLIC_SHA256,
        "frozen_examples_sha256": BIRD_INTERACT_EXAMPLES_SHA256,
        "qualification_protocol": BIRD_INTERACT_PROTOCOL,
        "official_score": False,
        "official_score_blocker": "Public release omits ground-truth test cases",
        "dialect": "postgresql",
        "suite": "frozen-example-qualification",
        "track": "rdst-ask",
        "context_mode": "raw-database-schema",
        "interaction_mode": BIRD_INTERACT_INTERACTION_MODE,
        "simulator_answer_sources": [
            "official-example-transcript",
            "public-non-critical-label",
            "official-unanswerable-fallback",
        ],
        "case_ids": [case.instance_id for case in cases],
        "excluded_capabilities": BIRD_INTERACT_EXCLUDED_CAPABILITIES,
        "repetitions": 1,
        "models": [asdict(model_spec)],
        "filter_model": asdict(filter_spec),
        "route_checks": route_checks,
        "database_provision": database_provision,
        "query_bounds": asdict(executor.bounds),
        "product_max_rows": product_max_rows,
        "product_enforce_result_limit": True,
        "execution_scoring_sql": "last AskSqlGeneratedEvent before display LIMIT",
        "reference_result_fingerprints": reference_fingerprints,
        "execution_correctness_policy": (
            "ordered rows when public non_critical_ambiguity contains "
            "sort_ambiguity; otherwise BIRD set equality"
        ),
    }
    store.initialize(manifest)

    aliases = {alias: filter_spec for alias in filter_aliases}
    adapter = AnthropicSDKAdapter(model_spec, model_aliases=aliases)
    adapter.set_call_receipt_sink(store.append_call_receipt)
    completed = store.completed_keys()
    with store.run_lock():
        for case in cases:
            attempt_key = _attempt_key(case, model_spec, protocol_sha256)
            if attempt_key in completed:
                continue
            attempt_id = str(uuid4())
            adapter.set_attempt_id(attempt_id)
            started = time.perf_counter()
            try:
                attempt = _run_case(
                    run_id=run_id,
                    attempt_id=attempt_id,
                    attempt_key=attempt_key,
                    case=case,
                    adapter=adapter,
                    executor=executor,
                    reference=references[case.instance_id],
                    product_max_rows=product_max_rows,
                )
            except Exception as exc:  # noqa: BLE001 - persist resumable failure
                attempt = {
                    "attempt_key": attempt_key,
                    "invocation_id": attempt_id,
                    "run_id": run_id,
                    "instance_id": case.instance_id,
                    "db_id": case.database,
                    "outcome": "harness_error",
                    "scored": False,
                    "error": str(exc),
                }
            finally:
                adapter.set_attempt_id(None)
            attempt["latency_ms"] = (time.perf_counter() - started) * 1000
            store.append_attempt(attempt)

        attempts = store.load_attempts()
        summary = build_frozen_qualification_summary(
            attempts,
            store.load_call_receipts(),
            expected_cases=len(cases),
        )
        store.write_summary(summary, render_frozen_qualification_markdown(summary))
    return summary


def rescore_frozen_qualification(
    *,
    source_run_dir: Path,
    output_dir: Path,
    run_id: str,
    cases: list[BirdInteractCase],
    protocol_sha256: str,
    rdst_revision: str,
    rdst_dirty: bool,
    rdst_diff_sha256: str,
) -> dict[str, Any]:
    source_store = ArtifactStore(source_run_dir)
    if not source_store.manifest_path.is_file():
        raise ValueError(f"Source qualification manifest is missing: {source_run_dir}")
    source_manifest = json.loads(source_store.manifest_path.read_text(encoding="utf-8"))
    expected_identity = {
        "dataset_revision": BIRD_INTERACT_PUBLIC_REVISION,
        "source_revision": BIRD_INTERACT_SOURCE_REVISION,
        "public_data_sha256": BIRD_INTERACT_PUBLIC_SHA256,
        "frozen_examples_sha256": BIRD_INTERACT_EXAMPLES_SHA256,
        "official_score": False,
    }
    for field, expected in expected_identity.items():
        if source_manifest.get(field) != expected:
            raise ValueError(f"Source qualification has incompatible {field}")
    if source_manifest.get("qualification_protocol") != (
        "frozen-example-transcript-replay-v2"
    ):
        raise ValueError("Only the v2 paid qualification can be rescored")
    if source_manifest.get("case_ids") != [case.instance_id for case in cases]:
        raise ValueError("Source qualification case IDs differ")

    source_attempts = source_store.load_attempts()
    attempts_by_id = {
        str(attempt.get("instance_id")): attempt for attempt in source_attempts
    }
    if set(attempts_by_id) != {case.instance_id for case in cases}:
        raise ValueError("Source qualification does not have one attempt per case")
    for case in cases:
        attempt = attempts_by_id[case.instance_id]
        if not attempt.get("scored") or "ordered_correct" not in attempt:
            raise ValueError(
                f"Source attempt {case.instance_id} cannot be rescored from stored output"
            )

    derived_from = {
        "run_id": source_manifest["run_id"],
        "manifest_sha256": _file_sha256(source_store.manifest_path),
        "attempts_sha256": _file_sha256(source_store.attempts_path),
        "calls_sha256": _file_sha256(source_store.call_receipts_path),
        "qualification_protocol": source_manifest["qualification_protocol"],
        "new_model_calls": 0,
    }
    manifest = {
        **source_manifest,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "rdst_revision": rdst_revision,
        "rdst_dirty": rdst_dirty,
        "rdst_diff_sha256": rdst_diff_sha256,
        "benchmark_protocol_sha256": protocol_sha256,
        "qualification_protocol": BIRD_INTERACT_RESCORE_PROTOCOL,
        "execution_correctness_policy": (
            "ordered rows when public non_critical_ambiguity contains "
            "sort_ambiguity; otherwise BIRD set equality"
        ),
        "derived_from": derived_from,
    }
    store = ArtifactStore(output_dir)
    store.initialize(manifest)
    with store.run_lock():
        completed = store.completed_keys()
        for case in cases:
            source = attempts_by_id[case.instance_id]
            key = _sha256_text(
                json.dumps(
                    {
                        "protocol": BIRD_INTERACT_RESCORE_PROTOCOL,
                        "protocol_sha256": protocol_sha256,
                        "source_attempt_key": source["attempt_key"],
                        "order_sensitive": case.order_sensitive,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            if key in completed:
                continue
            rescored = copy.deepcopy(source)
            execution_correct = (
                bool(source["ordered_correct"])
                if case.order_sensitive
                else bool(source["execution_correct"])
            )
            rescored.update(
                {
                    "attempt_key": key,
                    "source_attempt_key": source["attempt_key"],
                    "source_execution_correct": source["execution_correct"],
                    "order_sensitive": case.order_sensitive,
                    "execution_correct": execution_correct,
                    "outcome": ("correct" if execution_correct else "incorrect_result"),
                    "rescored_without_model_call": True,
                }
            )
            store.append_attempt(rescored)
        if not store.load_call_receipts():
            for call in source_store.load_call_receipts():
                inherited = {**call, "inherited_from_run_id": source_manifest["run_id"]}
                store.append_call_receipt(inherited)
        attempts = store.load_attempts()
        summary = build_frozen_qualification_summary(
            attempts,
            store.load_call_receipts(),
            expected_cases=len(cases),
        )
        summary["derived_from"] = derived_from
        store.write_summary(summary, render_frozen_qualification_markdown(summary))
    return summary


def build_frozen_qualification_summary(
    attempts: list[dict[str, Any]],
    calls: list[dict[str, Any]],
    *,
    expected_cases: int,
) -> dict[str, Any]:
    scored = [attempt for attempt in attempts if attempt.get("scored")]
    completed = len(attempts)
    correct = sum(bool(attempt.get("execution_correct")) for attempt in scored)
    asked = [
        question
        for attempt in scored
        for question in attempt.get("clarification_questions", [])
    ]
    matched = [question for question in asked if question.get("matched")]
    unanswerable = [
        question
        for question in asked
        if question.get("answer_mode") == "official-unanswerable-fallback"
    ]
    target_total = sum(int(attempt.get("target_term_count", 0)) for attempt in scored)
    target_covered = sum(
        int(attempt.get("covered_target_term_count", 0)) for attempt in scored
    )
    total_cost = sum(
        (float(call.get("normalized_cold_cost_usd") or 0) for call in calls),
        0.0,
    )
    stage_counts = Counter(str(call.get("stage", "unknown")) for call in calls)
    return {
        "publication_status": "qualification-only-not-official",
        "expected_cases": expected_cases,
        "completed_cases": completed,
        "scored_cases": len(scored),
        "coverage": completed / expected_cases if expected_cases else 0.0,
        "execution_correct": correct,
        "execution_accuracy": correct / len(scored) if scored else 0.0,
        "clarification_questions": len(asked),
        "matched_clarification_questions": len(matched),
        "question_relevance_rate": len(matched) / len(asked) if asked else None,
        "unanswerable_clarification_questions": len(unanswerable),
        "unanswerable_question_rate": (
            len(unanswerable) / len(asked) if asked else None
        ),
        "target_terms": target_total,
        "covered_target_terms": target_covered,
        "target_coverage": target_covered / target_total if target_total else None,
        "mean_answered_turns": (
            sum(int(attempt.get("answered_turns", 0)) for attempt in scored)
            / len(scored)
            if scored
            else 0.0
        ),
        "mean_latency_ms": (
            sum(float(attempt.get("latency_ms", 0)) for attempt in attempts) / completed
            if completed
            else 0.0
        ),
        "model_calls": len(calls),
        "calls_by_stage": dict(sorted(stage_counts.items())),
        "normalized_cold_cost_usd": total_cost,
        "attempts": attempts,
        "limitations": [
            "This is a pinned public-label qualification, not the official c-Interact evaluator.",
            "The public release omits ground-truth test cases, so accepted example SQL is used as the execution reference.",
            "Only two read-only query tasks are in scope; three successful CRUD/DDL examples are excluded by AskService's safety contract.",
            "Published example answers cover critical and knowledge ambiguities; deterministic answers derived from public SQL snippets cover supported non-critical labels.",
            "AskService batches clarification questions into one event; each question is counted as one simulated user turn.",
            "Semantic EX uses the last generated SQL before the product's display LIMIT; the actually executed limited SQL is recorded separately.",
        ],
    }


def render_frozen_qualification_markdown(summary: dict[str, Any]) -> str:
    relevance = summary["question_relevance_rate"]
    target_coverage = summary["target_coverage"]
    lines = [
        "# BIRD-Interact Frozen-Transcript Qualification",
        "",
        f"Publication status: **{summary['publication_status']}**",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Completed cases | {summary['completed_cases']}/{summary['expected_cases']} |",
        f"| Scored cases | {summary['scored_cases']} |",
        f"| Execution accuracy | {summary['execution_correct']}/{summary['scored_cases']} ({summary['execution_accuracy']:.1%}) |",
        f"| Clarification questions | {summary['clarification_questions']} |",
        f"| Relevant/matched questions | {summary['matched_clarification_questions']}/{summary['clarification_questions']} ({relevance:.1%}) |"
        if relevance is not None
        else "| Relevant/matched questions | n/a |",
        f"| Official out-of-scope responses | {summary['unanswerable_clarification_questions']}/{summary['clarification_questions']} ({summary['unanswerable_question_rate']:.1%}) |"
        if summary["unanswerable_question_rate"] is not None
        else "| Official out-of-scope responses | n/a |",
        f"| Labeled target coverage | {summary['covered_target_terms']}/{summary['target_terms']} ({target_coverage:.1%}) |"
        if target_coverage is not None
        else "| Labeled target coverage | n/a |",
        f"| Mean answered turns | {summary['mean_answered_turns']:.2f} |",
        f"| Mean product latency | {summary['mean_latency_ms'] / 1000:.2f}s |",
        f"| Model calls | {summary['model_calls']} |",
        f"| Normalized cold cost | ${summary['normalized_cold_cost_usd']:.6f} |",
        "",
        "## Per case",
        "",
        "| Case | Outcome | EX | Asked | Answered | Target coverage |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for attempt in summary["attempts"]:
        target_count = int(attempt.get("target_term_count", 0))
        covered = int(attempt.get("covered_target_term_count", 0))
        coverage = covered / target_count if target_count else 0.0
        lines.append(
            f"| {attempt.get('instance_id')} | {attempt.get('outcome')} | "
            f"{'yes' if attempt.get('execution_correct') else 'no'} | "
            f"{len(attempt.get('clarification_questions', []))} | "
            f"{attempt.get('answered_turns', 0)} | {covered}/{target_count} ({coverage:.1%}) |"
        )
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in summary["limitations"])
    lines.append("")
    return "\n".join(lines)


def _run_case(
    *,
    run_id: str,
    attempt_id: str,
    attempt_key: str,
    case: BirdInteractCase,
    adapter: AnthropicSDKAdapter,
    executor: PostgresQualificationExecutor,
    reference: QueryResult,
    product_max_rows: int,
) -> dict[str, Any]:
    target_config = {
        "engine": "postgresql",
        "host": executor.config.host,
        "port": executor.config.port,
        "user": executor.config.user,
        "password": executor.config.password,
        "database": case.database,
    }
    observed: dict[str, Any] = {"phases": [], "snapshots": [], "context": None}

    def observe(phase: str, ctx: Any) -> None:
        observed["phases"].append(phase)
        observed["context"] = ctx
        snapshot = (
            ctx.to_dict() if callable(getattr(ctx, "to_dict", None)) else vars(ctx)
        )
        observed["snapshots"].append(
            {"phase": phase, "context": copy.deepcopy(snapshot)}
        )

    service = AskService(
        llm_manager=adapter,
        db_executor=executor.as_ask_executor(case.database),
        targets_config_factory=lambda: _QualificationTargetsConfig(
            case.database, target_config
        ),
        persist_queries=False,
        session_store={},
        phase_observer=observe,
    )
    options = AskOptions(
        timeout_seconds=executor.bounds.timeout_seconds,
        max_rows=product_max_rows,
        no_interactive=False,
        enforce_result_limit=True,
        persist_query=False,
        raise_unexpected_errors=True,
    )

    async def collect_initial():
        return [
            event
            async for event in service.ask(
                AskInput(
                    question=case.ambiguous_query,
                    target=case.database,
                    source="bird-interact-qualification",
                ),
                options,
            )
        ]

    events = asyncio.run(collect_initial())
    clarification = next(
        (event for event in events if isinstance(event, AskClarificationNeededEvent)),
        None,
    )
    simulator = FrozenTranscriptSimulator(case)
    question_records = []
    answers: dict[str, str] = {}
    covered_terms: set[str] = set()
    if clarification is not None:
        if len(clarification.questions) > case.max_clarification_turns:
            service.abandon(clarification.session_id)
            return _base_attempt(
                run_id,
                attempt_id,
                attempt_key,
                case,
                observed,
                outcome="turn_budget_exceeded",
                scored=True,
                error=(
                    f"AskService requested {len(clarification.questions)} turns; "
                    f"budget is {case.max_clarification_turns}"
                ),
                question_records=[],
                covered_terms=set(),
            )
        for question in clarification.questions:
            match = simulator.answer(question.question, question.options)
            record = {
                "answer_key": question.id,
                "question": question.question,
                "options": question.options,
                "question_sha256": _sha256_text(question.question),
                "matched": match is not None,
            }
            if match is not None:
                record.update(
                    {key: value for key, value in match.items() if key != "answer"}
                )
                answers[question.id] = str(match["answer"])
                covered_terms.update(match["matched_target_terms"])
                record["answer_mode"] = "frozen-transcript"
            else:
                answers[question.id] = BIRD_INTERACT_UNANSWERABLE_RESPONSE
                record.update(
                    {
                        "answer_mode": "official-unanswerable-fallback",
                        "source_answer_sha256": _sha256_text(
                            BIRD_INTERACT_UNANSWERABLE_RESPONSE
                        ),
                    }
                )
            question_records.append(record)

        async def collect_resume():
            return [
                event
                async for event in service.resume(
                    clarification.session_id,
                    answers,
                )
            ]

        events.extend(asyncio.run(collect_resume()))

    error_event = next(
        (event for event in reversed(events) if isinstance(event, AskErrorEvent)),
        None,
    )
    result_event = next(
        (event for event in reversed(events) if isinstance(event, AskResultEvent)),
        None,
    )
    generated_event = next(
        (
            event
            for event in reversed(events)
            if isinstance(event, AskSqlGeneratedEvent)
        ),
        None,
    )
    base = _base_attempt(
        run_id,
        attempt_id,
        attempt_key,
        case,
        observed,
        outcome="pending",
        scored=True,
        error=error_event.message if error_event else None,
        question_records=question_records,
        covered_terms=covered_terms,
    )
    if result_event is None:
        base["outcome"] = "product_error" if error_event else "no_result"
        return base

    semantic_sql = (
        generated_event.sql if generated_event is not None else result_event.sql
    )
    candidate = executor.execute(semantic_sql, db_id=case.database)
    score = score_results(candidate, reference)
    execution_correct = (
        score.ordered_correct if case.order_sensitive else score.execution_correct
    )
    base.update(
        {
            "outcome": "correct" if execution_correct else "incorrect_result",
            "generated_sql": semantic_sql,
            "product_executed_sql": result_event.sql,
            "execution_correct": execution_correct,
            "soft_f1": score.soft_f1,
            "multiset_correct": score.multiset_correct,
            "ordered_correct": score.ordered_correct,
            "candidate_row_count": score.candidate_row_count,
            "reference_row_count": score.gold_row_count,
            "candidate_execution_error": candidate.error,
            "limit_added": result_event.limit_added,
            "order_sensitive": case.order_sensitive,
        }
    )
    return base


def _base_attempt(
    run_id: str,
    attempt_id: str,
    attempt_key: str,
    case: BirdInteractCase,
    observed: dict[str, Any],
    *,
    outcome: str,
    scored: bool,
    error: str | None,
    question_records: list[dict[str, Any]],
    covered_terms: set[str],
) -> dict[str, Any]:
    context = observed.get("context")
    return {
        "attempt_key": attempt_key,
        "invocation_id": attempt_id,
        "run_id": run_id,
        "instance_id": case.instance_id,
        "db_id": case.database,
        "track": "rdst-ask",
        "interaction_mode": BIRD_INTERACT_INTERACTION_MODE,
        "outcome": outcome,
        "scored": scored,
        "execution_correct": False,
        "order_sensitive": case.order_sensitive,
        "error": error,
        "target_terms": list(case.target_terms),
        "target_term_count": len(case.target_terms),
        "covered_target_terms": sorted(covered_terms),
        "covered_target_term_count": len(covered_terms),
        "clarification_questions": question_records,
        "answered_turns": len(question_records),
        "ask_service_phases": observed["phases"],
        "ask_service_phase_snapshots": observed["snapshots"],
        "schema_sha256": (
            _sha256_text(str(getattr(context, "schema_formatted", "")))
            if context is not None
            else None
        ),
    }


def _phase_one_reference_and_exchanges(
    sample: dict[str, Any],
) -> tuple[str, list[tuple[str, str]]]:
    exchanges = []
    for step in sample.get("tool_trajectory", []):
        if step.get("tool") == "ask_user":
            exchanges.append(
                (
                    str(step.get("args", {}).get("question", "")),
                    str(step.get("result", "")),
                )
            )
        elif step.get("tool") == "submit_sql" and str(
            step.get("result", "")
        ).startswith("Phase 1 correct"):
            reference_sql = str(step.get("args", {}).get("sql", "")).strip()
            if not reference_sql:
                raise ValueError("Accepted phase-1 tool call has no SQL")
            return reference_sql, exchanges
    raise ValueError("Frozen example has no accepted phase-1 submit_sql call")


def _verify_event_query(sample: dict[str, Any], expected_query: str) -> None:
    messages = [
        event.get("message", "")
        for event in sample.get("adk_events", [])
        if event.get("type") == "user_message"
    ]
    if not messages:
        raise ValueError(
            f"Frozen example {sample.get('instance_id')} has no user event"
        )
    first = str(messages[0])
    if not first.startswith("User Query:\n"):
        raise ValueError("Frozen example first user event has an unexpected format")
    event_query = first.removeprefix("User Query:\n").split("\n\nYou have", 1)[0]
    if " ".join(event_query.split()) != " ".join(expected_query.split()):
        raise ValueError(
            f"Frozen/public query mismatch for {sample.get('instance_id')}"
        )


def _task_target_terms(task: dict[str, Any]) -> tuple[str, ...]:
    entries = list(task.get("user_query_ambiguity", {}).get("critical_ambiguity", []))
    entries += list(
        task.get("user_query_ambiguity", {}).get("non_critical_ambiguity", [])
    )
    entries += list(task.get("knowledge_ambiguity", []))
    terms = []
    for entry in entries:
        term = str(entry.get("term", "")).strip()
        if term and term not in terms:
            terms.append(term)
    if not terms:
        raise ValueError(f"Task {task.get('instance_id')} has no ambiguity targets")
    return tuple(terms)


def _public_non_critical_exchanges(
    task: dict[str, Any],
    existing: tuple[FrozenExchange, ...],
) -> list[FrozenExchange]:
    exchanges = []
    entries = task.get("user_query_ambiguity", {}).get("non_critical_ambiguity", [])
    for entry in entries:
        term = str(entry.get("term", "")).strip()
        if not term or any(term in exchange.target_terms for exchange in existing):
            continue
        answer = _public_non_critical_answer(entry)
        if answer is None:
            continue
        exchanges.append(
            FrozenExchange(
                question=f"Clarify the intended meaning of: {term}",
                answer=answer,
                target_terms=(term,),
                source_kind="public-non-critical-label",
            )
        )
    return exchanges


def _public_non_critical_answer(entry: dict[str, Any]) -> str | None:
    ambiguity_type = str(entry.get("type", ""))
    sql_snippet = str(entry.get("sql_snippet", ""))
    if ambiguity_type == "sort_ambiguity":
        if re.search(r"\bDESC\b", sql_snippet, re.IGNORECASE):
            return "Sort from highest to lowest in descending order."
        if re.search(r"\bASC\b", sql_snippet, re.IGNORECASE):
            return "Sort from lowest to highest in ascending order."
    if ambiguity_type == "limit_ambiguity":
        match = re.search(r"\bLIMIT\s+(\d+)\b", sql_snippet, re.IGNORECASE)
        if match:
            return f"Return at most {match.group(1)} rows."
    return None


def _mentioned_terms(content: str, terms: tuple[str, ...]) -> set[str]:
    normalized = " ".join(_WORD.findall(content.casefold()))
    mentioned = set()
    for term in terms:
        aliases = _term_aliases(term)
        if any(
            " ".join(_WORD.findall(alias.casefold())) in normalized for alias in aliases
        ):
            mentioned.add(term)
    return mentioned


def _term_aliases(term: str) -> set[str]:
    aliases = {term}
    normalized_term = " ".join(_WORD.findall(term.casefold()))
    for prefix in ("sorted by ", "ordered by "):
        if normalized_term.startswith(prefix):
            subject = normalized_term.removeprefix(prefix)
            aliases.add(f"sort by {subject}")
            aliases.add(f"order by {subject}")
            aliases.add("sort direction")
            aliases.add("ascending or descending")
            aliases.add("descending or ascending")
            aliases.add("highest to lowest")
            aliases.add("lowest to highest")
    parenthetical = re.findall(r"\(([A-Za-z0-9_-]{2,})\)", term)
    aliases.update(parenthetical)
    words = [
        word
        for word in _WORD.findall(term.casefold())
        if word not in {"the", "of", "and"}
    ]
    if len(words) >= 3:
        aliases.add("".join(word[0] for word in words).upper())
    generic = {
        "event",
        "events",
        "factor",
        "index",
        "level",
        "marker",
        "profile",
        "profiles",
        "score",
    }
    if len(words) <= 2:
        aliases.update(word for word in words if len(word) >= 6 and word not in generic)
    return {alias for alias in aliases if alias}


def _token_overlap(left: str, right: str) -> float:
    left_tokens = set(_WORD.findall(left.casefold()))
    right_tokens = set(_WORD.findall(right.casefold()))
    union = left_tokens.union(right_tokens)
    return len(left_tokens.intersection(right_tokens)) / len(union) if union else 0.0


def _is_single_read_only_query(sql: str) -> bool:
    try:
        statements = sqlglot.parse(sql, read="postgres")
    except sqlglot.errors.ParseError:
        return False
    if len(statements) != 1:
        return False
    statement = statements[0]
    return isinstance(statement, (exp.Select, exp.Subquery, exp.Union))


def _attempt_key(
    case: BirdInteractCase, model_spec: ModelSpec, protocol_sha256: str
) -> str:
    payload = {
        "dataset_revision": BIRD_INTERACT_PUBLIC_REVISION,
        "source_revision": BIRD_INTERACT_SOURCE_REVISION,
        "protocol": BIRD_INTERACT_PROTOCOL,
        "protocol_sha256": protocol_sha256,
        "instance_id": case.instance_id,
        "model": model_spec.model,
        "model_name": model_spec.name,
    }
    return _sha256_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _postgres_execute_worker(
    config: PostgresConnectionConfig,
    bounds: PostgresQueryBounds,
    sql: str,
    db_id: str,
    result_queue,
) -> None:
    try:
        result = PostgresQualificationExecutor(config, bounds)._execute_direct(
            sql, db_id=db_id
        )
    except Exception as exc:  # noqa: BLE001 - propagate child-process failure
        result_queue.put(("error", repr(exc)))
    else:
        result_queue.put(("ok", result))


def _row_size(row: tuple[Any, ...]) -> int:
    return sum(
        1
        if value is None
        else len(value)
        if isinstance(value, bytes)
        else len(str(value).encode("utf-8"))
        for value in row
    )


def _require_sha256(path: Path, expected: str, label: str) -> None:
    if not path.is_file():
        raise ValueError(f"Missing {label}: {path}")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f"{label} hash mismatch: expected {expected}, got {actual}")


def _download_pinned_file(
    url: str,
    destination: Path,
    expected_sha256: str,
    label: str,
) -> None:
    if destination.is_file():
        _require_sha256(destination, expected_sha256, label)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    response = requests.get(url, stream=True, timeout=60)
    response.raise_for_status()
    with open(partial, "wb") as file_obj:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                file_obj.write(chunk)
        file_obj.flush()
        os.fsync(file_obj.fileno())
    try:
        _require_sha256(partial, expected_sha256, label)
    except (OSError, ValueError):
        partial.unlink(missing_ok=True)
        raise
    os.replace(partial, destination)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"Required artifact file is missing: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_preparation_receipt(
    path: Path,
    *,
    public_data_path: Path,
    examples_path: Path,
    image_id: str,
    image_digest: str,
) -> dict[str, Any]:
    cases = load_frozen_qualification_cases(public_data_path, examples_path)
    receipt = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_revision": BIRD_INTERACT_SOURCE_REVISION,
        "public_revision": BIRD_INTERACT_PUBLIC_REVISION,
        "public_data_sha256": BIRD_INTERACT_PUBLIC_SHA256,
        "frozen_examples_sha256": BIRD_INTERACT_EXAMPLES_SHA256,
        "postgres_image_id": image_id,
        "postgres_image_digest": image_digest,
        "qualification_case_ids": [case.instance_id for case in cases],
        "frozen_exchange_count": sum(len(case.exchanges) for case in cases),
        "excluded_capabilities": BIRD_INTERACT_EXCLUDED_CAPABILITIES,
    }
    write_json(path, receipt)
    return receipt

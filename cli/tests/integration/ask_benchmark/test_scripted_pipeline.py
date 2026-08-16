from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pymysql
import pytest

from devtools.ask_benchmark.artifacts import ArtifactStore
from devtools.ask_benchmark.executor import (
    MySQLConnectionConfig,
    MySQLExecutor,
    QueryBounds,
    database_user,
)
from devtools.ask_benchmark.models import (
    BenchmarkCase,
    ContextMode,
    EvaluationTrack,
    InteractionMode,
    ModelCallRecord,
    ModelSpec,
    Pricing,
)
from devtools.ask_benchmark.runner import BenchmarkRunner, _BenchmarkTargetsConfig
from devtools.ask_benchmark.schema import MySQLSchemaLoader
from features.ask.events import AskClarificationNeededEvent, AskResultEvent
from features.ask.models import AskInput, AskOptions
from features.ask.service import AskService
from features.schema.semantic_layer.manager import SemanticLayerManager

pytestmark = pytest.mark.skipif(
    os.getenv("RDST_ASK_BENCHMARK_INTEGRATION") != "1",
    reason="requires the isolated Ask benchmark MySQL service",
)

_DB_ID = "fixture"
_DATABASE = f"bird_{_DB_ID}"
_USER_PREFIX = "rdst_ci"
_PASSWORD = "rdst-ci"


def _spec() -> ModelSpec:
    return ModelSpec(
        name="scripted-sonnet",
        model="scripted/sonnet",
        transport="anthropic",
        provider_order=(),
        pricing=Pricing(Decimal(0), Decimal(0)),
        temperature=0.0,
        max_tokens=800,
    )


class ScriptedAdapter:
    """Deterministic adapter that still emits benchmark call receipts."""

    propagate_query_errors = True

    def __init__(self, spec: ModelSpec, *, generation_mode: str = "normal"):
        self.default_spec = spec
        self.generation_mode = generation_mode
        self.call_records: list[ModelCallRecord] = []
        self.prompts: list[tuple[str, str]] = []
        self._attempt_id: str | None = None
        self._receipt_sink: Callable[[ModelCallRecord], None] | None = None

    def set_attempt_id(self, attempt_id: str | None) -> None:
        self._attempt_id = attempt_id

    def set_call_receipt_sink(
        self, sink: Callable[[ModelCallRecord], None] | None
    ) -> None:
        self._receipt_sink = sink

    def query(self, *, user_query: str, purpose: str | None = None, **_kwargs):
        stage = purpose or "query"
        self.prompts.append((stage, user_query))
        if stage == "schema_filter_concepts":
            text = json.dumps(
                {
                    "suggested_tables": ["people"],
                    "reasoning": "The question asks for people.",
                }
            )
        else:
            text = "SELECT name FROM people ORDER BY id"
        self._record(stage, user_query, text)
        return {
            "text": text,
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
            "model": self.default_spec.model,
        }

    def generate_response(
        self, *, prompt: str, purpose: str | None = None, **_kwargs
    ) -> dict:
        stage = purpose or "generation"
        self.prompts.append((stage, prompt))
        if stage == "clarification":
            payload = {
                "ambiguities": [],
                "total_ambiguities": 0,
                "requires_clarification": False,
                "can_proceed_with_assumptions": True,
                "overall_confidence": 1.0,
            }
        elif stage == "sql_validation_repair":
            payload = {
                "sql": "SELECT name FROM people ORDER BY id",
                "explanation": "Return each name once in insertion order.",
            }
        else:
            sql = "SELECT name FROM people ORDER BY id"
            if self.generation_mode == "multiple-statements":
                sql = "SELECT name FROM people; SELECT id FROM people"
            elif self.generation_mode == "timeout":
                aliases = [f"p{index}" for index in range(1, 27)]
                sql = (
                    "SELECT SUM(CRC32(CONCAT_WS(':', "
                    + ", ".join(f"{alias}.id" for alias in aliases)
                    + "))) FROM "
                    + " CROSS JOIN ".join(f"people AS {alias}" for alias in aliases)
                )
            elif self.generation_mode == "interactive":
                sql = "SELECT name FROM people ORDER BY name ASC"
            payload = {
                "sql": sql,
                "explanation": "Return the requested rows.",
                "confidence": 1.0,
                "assumptions": [],
                "cannot_answer": False,
                "cannot_answer_reason": "",
                "missing_schema": [],
            }
        response = json.dumps(payload)
        self._record(stage, prompt, response)
        return {
            "response": response,
            "tokens_used": 15,
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
            },
            "model": self.default_spec.model,
        }

    def drain_call_records(self) -> list[ModelCallRecord]:
        records = self.call_records
        self.call_records = []
        return records

    def _record(self, stage: str, prompt: str, response: str) -> None:
        record = ModelCallRecord(
            stage=stage,
            transport="scripted",
            requested_model=self.default_spec.model,
            returned_model=self.default_spec.model,
            upstream_provider="scripted",
            request_id=f"scripted-{len(self.call_records) + 1}",
            prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
            response_sha256=hashlib.sha256(response.encode()).hexdigest(),
            input_tokens=10,
            output_tokens=5,
            reasoning_tokens=0,
            cached_input_tokens=0,
            latency_ms=0.0,
            transport_attempts=1,
            actual_cost_usd=Decimal(0),
            normalized_cold_cost_usd=Decimal(0),
            expected_billed_cost_usd=Decimal(0),
            model_name=self.default_spec.name,
            benchmark_model_name=self.default_spec.name,
            attempt_id=self._attempt_id,
        )
        self.call_records.append(record)
        if self._receipt_sink is not None:
            self._receipt_sink(record)


@pytest.fixture(scope="module")
def mysql_config() -> MySQLConnectionConfig:
    host = os.getenv("RDST_ASK_BENCHMARK_MYSQL_HOST", "127.0.0.1")
    port = int(os.getenv("RDST_ASK_BENCHMARK_MYSQL_PORT", "13317"))
    root_password = os.getenv(
        "RDST_ASK_BENCHMARK_MYSQL_ROOT_PASSWORD", "rdst-bird-root"
    )
    user = database_user(_USER_PREFIX, _DB_ID)
    connection = pymysql.connect(
        host=host,
        port=port,
        user="root",
        password=root_password,
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{_DATABASE}`")
            cursor.execute(f"CREATE DATABASE `{_DATABASE}`")
            cursor.execute(
                "CREATE TABLE `bird_fixture`.`people` ("
                "`id` INT PRIMARY KEY, `name` VARCHAR(64) NOT NULL)"
            )
            cursor.execute(
                "INSERT INTO `bird_fixture`.`people` (`id`, `name`) "
                "VALUES (1, 'Ada'), (2, 'Grace')"
            )
            cursor.execute(f"DROP USER IF EXISTS `{user}`@'%'")
            cursor.execute(f"CREATE USER `{user}`@'%%' IDENTIFIED BY %s", (_PASSWORD,))
            cursor.execute(
                f"GRANT SELECT, SHOW VIEW ON `{_DATABASE}`.* TO `{user}`@'%'"
            )
    finally:
        connection.close()
    return MySQLConnectionConfig(
        host=host,
        port=port,
        user=_USER_PREFIX,
        password=_PASSWORD,
        database_prefix="bird_",
    )


def _case(question_id: int, question: str, gold_sql: str) -> BenchmarkCase:
    return BenchmarkCase(
        question_id=question_id,
        db_id=_DB_ID,
        question=question,
        evidence="",
        gold_sql=gold_sql,
        difficulty="simple",
        dialect="mysql",
    )


def _runner(
    tmp_path: Path,
    mysql_config: MySQLConnectionConfig,
    *,
    track: EvaluationTrack,
    adapter: ScriptedAdapter,
) -> tuple[BenchmarkRunner, ArtifactStore]:
    store = ArtifactStore(tmp_path / track.value)
    store.initialize({"run_id": track.value})
    executor = MySQLExecutor(
        mysql_config,
        QueryBounds(timeout_seconds=1, max_rows=100, max_result_bytes=1024 * 1024),
    )
    runner = BenchmarkRunner(
        run_id=track.value,
        track=track,
        context_mode=ContextMode.RAW,
        executor=executor,
        schema_loader=MySQLSchemaLoader(mysql_config),
        artifact_store=store,
        filter_spec=_spec(),
        filter_aliases=(),
        semantic_dir=tmp_path / "semantic",
        interaction_mode=(
            InteractionMode.AUTO
            if track == EvaluationTrack.RDST
            else InteractionMode.NOT_APPLICABLE
        ),
        protocol_fingerprint="scripted-contract-v1",
        adapter_factory=lambda _spec, _aliases: adapter,
    )
    return runner, store


def test_paired_runner_uses_real_ask_service_and_persists_receipts(
    tmp_path: Path, mysql_config: MySQLConnectionConfig
) -> None:
    case = _case(
        1,
        "List every person's name in insertion order.",
        "SELECT name FROM people ORDER BY id",
    )
    attempts = {}
    stores = {}
    for track in (EvaluationTrack.MODEL_ONLY, EvaluationTrack.RDST):
        adapter = ScriptedAdapter(_spec())
        runner, store = _runner(tmp_path, mysql_config, track=track, adapter=adapter)
        cases, failures = runner.preflight_gold([case])
        assert failures == {}
        attempts[track] = runner.run(cases, [_spec()])
        stores[track] = store

    for track in (EvaluationTrack.MODEL_ONLY, EvaluationTrack.RDST):
        attempt = attempts[track][0]
        assert attempt["outcome"] == "correct"
        assert attempt["execution_correct"] is True
        assert stores[track].load_call_receipts()
        assert stores[track].load_call_receipts()[0]["response_sha256"]

    ask_attempt = attempts[EvaluationTrack.RDST][0]
    assert ask_attempt["diagnostics"]["ask_service_phases"] == [
        "schema",
        "filter",
        "clarify",
        "generate",
        "validate",
        "execute",
    ]


def test_real_ask_service_repairs_multiple_statements_once(
    tmp_path: Path, mysql_config: MySQLConnectionConfig
) -> None:
    case = _case(
        2,
        "Exercise the multiple statement repair and list every person's name.",
        "SELECT name FROM people ORDER BY id",
    )
    adapter = ScriptedAdapter(_spec(), generation_mode="multiple-statements")
    runner, _store = _runner(
        tmp_path, mysql_config, track=EvaluationTrack.RDST, adapter=adapter
    )
    cases, failures = runner.preflight_gold([case])

    attempts = runner.run(cases, [_spec()])

    assert failures == {}
    assert attempts[0]["outcome"] == "correct"
    assert attempts[0]["diagnostics"]["retry_count"] == 1
    assert [record["stage"] for record in attempts[0]["call_records"]] == [
        "sql_generation",
        "sql_validation_repair",
    ]


def test_real_ask_service_records_execution_timeout(
    tmp_path: Path, mysql_config: MySQLConnectionConfig
) -> None:
    case = _case(3, "Exercise the slow query timeout.", "SELECT 1")
    adapter = ScriptedAdapter(_spec(), generation_mode="timeout")
    runner, _store = _runner(
        tmp_path, mysql_config, track=EvaluationTrack.RDST, adapter=adapter
    )
    cases, failures = runner.preflight_gold([case])

    attempts = runner.run(cases, [_spec()])

    assert failures == {}
    assert attempts[0]["outcome"] == "timeout", (
        attempts[0]["generated_sql"],
        attempts[0]["error"],
        attempts[0]["diagnostics"],
    )
    assert attempts[0]["failure_stage"] == "execute"


def test_real_interactive_ask_resumes_with_reviewed_answer(
    tmp_path: Path, mysql_config: MySQLConnectionConfig
) -> None:
    adapter = ScriptedAdapter(_spec(), generation_mode="interactive")
    executor = MySQLExecutor(mysql_config, QueryBounds(timeout_seconds=2, max_rows=100))
    target_config = {
        "engine": "mysql",
        "host": mysql_config.host,
        "port": mysql_config.port,
        "user": mysql_config.user_for(_DB_ID),
        "password": mysql_config.password,
        "database": mysql_config.database_for(_DB_ID),
    }
    sessions = {}
    service = AskService(
        llm_manager=adapter,
        semantic_manager=SemanticLayerManager(base_dir=tmp_path / "semantic"),
        db_executor=executor.as_ask_executor(_DB_ID),
        targets_config_factory=lambda: _BenchmarkTargetsConfig(_DB_ID, target_config),
        session_store=sessions,
        persist_queries=False,
    )
    options = AskOptions(
        timeout_seconds=2,
        max_rows=100,
        no_interactive=False,
        enforce_result_limit=False,
        persist_query=False,
        raise_unexpected_errors=True,
    )

    async def run_flow():
        first = [
            event
            async for event in service.ask(
                AskInput(
                    question="List people sorted by name.",
                    target=_DB_ID,
                    source="benchmark-integrity",
                ),
                options,
            )
        ]
        clarification = next(
            event for event in first if isinstance(event, AskClarificationNeededEvent)
        )
        question = clarification.questions[0]
        resumed = [
            event
            async for event in service.resume(
                clarification.session_id,
                {question.id: "Sort from lowest to highest in ascending order."},
            )
        ]
        return clarification, resumed

    clarification, resumed = asyncio.run(run_flow())

    assert clarification.questions[0].id == "missing_sql_keywords"
    result = next(event for event in resumed if isinstance(event, AskResultEvent))
    assert result.rows == [["Ada"], ["Grace"]]
    assert sessions == {}
    generation_prompt = next(
        prompt for stage, prompt in adapter.prompts if stage == "sql_generation"
    )
    assert "lowest to highest" in generation_prompt

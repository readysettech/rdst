import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from devtools.ask_benchmark.artifacts import ArtifactStore
from devtools.ask_benchmark.executor import (
    MySQLConnectionConfig,
    MySQLExecutor,
    QueryBounds,
)
from devtools.ask_benchmark.models import (
    BenchmarkCase,
    ContextMode,
    EvaluationTrack,
    InteractionMode,
    ModelCallRecord,
    ModelSpec,
    Pricing,
    QueryResult,
    RunLimits,
)
from devtools.ask_benchmark.pipeline import (
    build_model_only_prompt,
    extract_sql,
    provided_context_for_case,
    question_for_rdst,
)
from devtools.ask_benchmark.pydantic_adapter import BenchmarkTransportError
from devtools.ask_benchmark.runner import (
    BenchmarkRunner,
    _multiset_result_hash,
    _sql_tables,
)
from features.ask.engine.ask3.phases.schema import format_semantic_schema_compact
from features.ask.engine.ask3.types import SchemaInfo, SchemaSource
from features.ask.events import (
    AskClarificationNeededEvent,
    AskErrorEvent,
    AskResultEvent,
    AskSqlGeneratedEvent,
)
from features.ask.models import AskClarificationQuestion
from features.schema.semantic_layer.manager import SemanticLayerManager


def test_sql_table_diagnostics_exclude_cte_aliases() -> None:
    sql = """
    WITH lap_times_in_seconds AS (
      SELECT race_id FROM lap_times
    )
    SELECT race_id FROM lap_times_in_seconds
    """

    assert _sql_tables(sql, "mysql") == {"lap_times"}


class _Adapter:
    def __init__(self, spec):
        self.default_spec = spec

    def query(self, **_kwargs):
        return {"text": "```sql\nSELECT 1\n```"}

    def drain_call_records(self):
        return []


class _ReceiptAdapter(_Adapter):
    def __init__(self, spec, *, cost=Decimal("0.25")):
        super().__init__(spec)
        self.cost = cost
        self.call_records = []
        self.receipt_sink = None
        self.attempt_id = None

    def set_call_receipt_sink(self, sink):
        self.receipt_sink = sink

    def set_attempt_id(self, attempt_id):
        self.attempt_id = attempt_id

    def query(self, **_kwargs):
        record = ModelCallRecord(
            stage="model_only_generation",
            transport="scripted",
            requested_model=self.default_spec.model,
            returned_model=self.default_spec.model,
            upstream_provider="scripted",
            request_id=f"request-{len(self.call_records) + 1}",
            prompt_sha256="0" * 64,
            response_sha256="1" * 64,
            input_tokens=1,
            output_tokens=1,
            reasoning_tokens=0,
            cached_input_tokens=0,
            latency_ms=0,
            transport_attempts=1,
            actual_cost_usd=self.cost,
            normalized_cold_cost_usd=self.cost,
            expected_billed_cost_usd=self.cost,
            model_name=self.default_spec.name,
            benchmark_model_name=self.default_spec.name,
            attempt_id=self.attempt_id,
        )
        self.call_records.append(record)
        if self.receipt_sink:
            self.receipt_sink(record)
        return {"text": "SELECT 1"}

    def drain_call_records(self):
        records = self.call_records
        self.call_records = []
        return records


class _Executor:
    def execute(self, _sql, *, db_id):
        assert db_id == "fixture"
        return QueryResult(columns=("value",), rows=((1,),))


class _SchemaLoader:
    def load(self, db_id):
        assert db_id == "fixture"
        return "Dialect: MySQL\n\nTable: `fixture_table`\n  `value` INT"


class _RDSTExecutor(_Executor):
    config = MySQLConnectionConfig(
        "localhost", 3306, "user", "password", database_prefix="bird_"
    )
    bounds = QueryBounds()

    def as_ask_executor(self, db_id):
        assert db_id == "fixture"
        return self


def _rdst_context():
    return SimpleNamespace(
        schema_source=SchemaSource.DATABASE,
        schema_info=SchemaInfo(
            target="fixture",
            db_type="mysql",
            source=SchemaSource.DATABASE,
            tables={"fixture_table": SimpleNamespace()},
        ),
        retry_count=0,
        generation_confidence=1.0,
        limit_added=False,
        limit_reduced=False,
        generated_sql="SELECT 1",
        sql="SELECT 1",
    )


def _spec():
    return ModelSpec(
        name="fixture-model",
        model="fixture/model",
        transport="openrouter",
        provider_order=("fixture",),
        pricing=Pricing(Decimal(0), Decimal(0)),
    )


def _case():
    return BenchmarkCase(
        question_id=1,
        db_id="fixture",
        question="Return one",
        evidence="one means 1",
        gold_sql="SELECT 1",
        difficulty="simple",
        dialect="mysql",
    )


def _rdst_runner(
    tmp_path,
    interaction_mode=InteractionMode.INTERACTIVE_NO_ANSWER,
    context_mode=ContextMode.RAW,
    semantic_schema_format="verbose-v1",
    ask_accuracy_profile="baseline",
):
    store = ArtifactStore(tmp_path / "run")
    store.initialize({"run_id": "run"})
    spec = _spec()
    return BenchmarkRunner(
        run_id="run",
        track=EvaluationTrack.RDST,
        context_mode=context_mode,
        executor=_RDSTExecutor(),
        schema_loader=_SchemaLoader(),
        artifact_store=store,
        filter_spec=spec,
        filter_aliases=(),
        semantic_dir=tmp_path / "semantic",
        interaction_mode=interaction_mode,
        adapter_factory=lambda model, _aliases: _Adapter(model),
        semantic_schema_format=semantic_schema_format,
        ask_accuracy_profile=ask_accuracy_profile,
    )


def test_query_worker_enforces_wall_clock_timeout(monkeypatch):
    def hang(_self, _sql, *, db_id):
        assert db_id == "fixture"
        time.sleep(1)

    monkeypatch.setattr(MySQLExecutor, "_execute_direct", hang)
    executor = MySQLExecutor(
        MySQLConnectionConfig("localhost", 3306, "user", "password"),
        QueryBounds(timeout_seconds=0.05),
    )

    result = executor.execute("SELECT 1", db_id="fixture")

    assert result.timed_out is True
    assert result.error == "Query exceeded benchmark wall-clock timeout"


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("SELECT 1", "SELECT 1"),
        ("SQL: SELECT 1", "SELECT 1"),
        ("```sql\nSELECT 1\n```", "SELECT 1"),
        ('{"sql": "SELECT 1"}', "SELECT 1"),
        ("<think>reasoning</think>SELECT 1", "SELECT 1"),
        ("(SELECT 1) UNION (SELECT 2)", "(SELECT 1) UNION (SELECT 2)"),
    ],
)
def test_extract_sql(response, expected):
    assert extract_sql(response) == expected


def test_extract_sql_rejects_prose():
    with pytest.raises(ValueError, match="SELECT or WITH"):
        extract_sql("I cannot answer this question")


def test_evidence_is_a_first_class_authoritative_context_for_both_tracks():
    case = _case()
    _system, direct_prompt = build_model_only_prompt(
        case, "Table: fixture", ContextMode.EVIDENCE
    )

    assert question_for_rdst(case, ContextMode.EVIDENCE) == case.question
    assert provided_context_for_case(case, ContextMode.EVIDENCE) == case.evidence
    assert direct_prompt.count(case.evidence) == 1
    assert "AUTHORITATIVE CALLER-PROVIDED CONTEXT:" in direct_prompt
    assert "BIRD evidence:" not in direct_prompt

    _system, no_context_prompt = build_model_only_prompt(
        case, "Table: fixture", ContextMode.BIRD_CURATED
    )
    assert provided_context_for_case(case, ContextMode.BIRD_CURATED) == ""
    assert "AUTHORITATIVE CALLER-PROVIDED CONTEXT:" not in no_context_prompt


def test_matched_database_values_are_labeled_separately_for_direct_track():
    matched_values = (
        "These exact values occur in the database.\n- 'Monterey': schools.County"
    )

    _system, prompt = build_model_only_prompt(
        _case(),
        "Table: schools",
        ContextMode.AUTO_INIT_PROFILED_VALUES,
        matched_database_values=matched_values,
    )

    assert prompt.count(matched_values) == 1
    assert "QUESTION-MATCHED DATABASE VALUES:" in prompt
    assert "AUTHORITATIVE CALLER-PROVIDED CONTEXT:" not in prompt


def test_gold_fingerprint_ignores_unordered_row_order():
    first = QueryResult(columns=("value",), rows=((1,), (2,), (1,)))
    reordered = QueryResult(columns=("value",), rows=((2,), (1,), (1,)))

    assert _multiset_result_hash(first) == _multiset_result_hash(reordered)


def test_model_only_runner_scores_and_persists_a_fixture(tmp_path: Path):
    store = ArtifactStore(tmp_path / "run")
    store.initialize({"run_id": "run"})
    spec = _spec()
    runner = BenchmarkRunner(
        run_id="run",
        track=EvaluationTrack.MODEL_ONLY,
        context_mode=ContextMode.RAW,
        executor=_Executor(),
        schema_loader=_SchemaLoader(),
        artifact_store=store,
        filter_spec=spec,
        filter_aliases=(),
        semantic_dir=tmp_path / "semantic",
        adapter_factory=lambda model, _aliases: _Adapter(model),
    )
    cases, failures = runner.preflight_gold([_case()])

    attempts = runner.run(cases, [spec])

    assert failures == {}
    assert len(attempts) == 1
    assert attempts[0]["outcome"] == "correct"
    assert attempts[0]["execution_correct"] is True
    assert runner.run(cases, [spec]) == attempts


def test_runner_stops_at_cumulative_provider_call_limit(tmp_path: Path):
    store = ArtifactStore(tmp_path / "run")
    store.initialize({"run_id": "run"})
    spec = _spec()
    runner = BenchmarkRunner(
        run_id="run",
        track=EvaluationTrack.MODEL_ONLY,
        context_mode=ContextMode.RAW,
        executor=_Executor(),
        schema_loader=_SchemaLoader(),
        artifact_store=store,
        filter_spec=spec,
        filter_aliases=(),
        semantic_dir=tmp_path / "semantic",
        adapter_factory=lambda model, _aliases: _ReceiptAdapter(model),
        run_limits=RunLimits(max_provider_calls=1),
    )
    second = BenchmarkCase(
        question_id=2,
        db_id="fixture",
        question="Return one again",
        evidence="",
        gold_sql="SELECT 1",
        difficulty="simple",
        dialect="mysql",
    )
    cases, failures = runner.preflight_gold([_case(), second])

    attempts = runner.run(cases, [spec])

    assert failures == {}
    assert len(attempts) == 1
    assert attempts[0]["outcome"] == "correct"
    assert len(store.load_call_receipts()) == 1
    assert runner.budget_stop_reason == "provider-call limit reached (1/1)"
    assert runner.run(cases, [spec]) == attempts
    assert len(store.load_call_receipts()) == 1


def test_paid_run_limits_require_durable_adapter_receipts(tmp_path: Path):
    store = ArtifactStore(tmp_path / "run")
    store.initialize({"run_id": "run"})
    spec = _spec()
    runner = BenchmarkRunner(
        run_id="run",
        track=EvaluationTrack.MODEL_ONLY,
        context_mode=ContextMode.RAW,
        executor=_Executor(),
        schema_loader=_SchemaLoader(),
        artifact_store=store,
        filter_spec=spec,
        filter_aliases=(),
        semantic_dir=tmp_path / "semantic",
        adapter_factory=lambda model, _aliases: _Adapter(model),
        run_limits=RunLimits(max_provider_calls=1),
    )
    cases, _ = runner.preflight_gold([_case()])

    with pytest.raises(RuntimeError, match="durable call receipts"):
        runner.run(cases, [spec])


def test_runner_stops_on_normalized_cost_at_completed_call_boundary(
    tmp_path: Path,
):
    store = ArtifactStore(tmp_path / "run")
    store.initialize({"run_id": "run"})
    spec = _spec()
    runner = BenchmarkRunner(
        run_id="run",
        track=EvaluationTrack.MODEL_ONLY,
        context_mode=ContextMode.RAW,
        executor=_Executor(),
        schema_loader=_SchemaLoader(),
        artifact_store=store,
        filter_spec=spec,
        filter_aliases=(),
        semantic_dir=tmp_path / "semantic",
        adapter_factory=lambda model, _aliases: _ReceiptAdapter(model),
        run_limits=RunLimits(max_normalized_cost_usd=Decimal("0.20")),
    )
    second = BenchmarkCase(
        question_id=2,
        db_id="fixture",
        question="Return one again",
        evidence="",
        gold_sql="SELECT 1",
        difficulty="simple",
        dialect="mysql",
    )
    cases, _ = runner.preflight_gold([_case(), second])

    attempts = runner.run(cases, [spec])

    assert attempts[0]["outcome"] == "correct"
    assert runner.budget_stop_reason == ("normalized-cost limit reached ($0.25/$0.20)")


def test_rdst_runner_consumes_ask_service_result_events(tmp_path: Path):
    ctx = _rdst_context()

    class FakeAskService:
        def __init__(self, **kwargs):
            self.observer = kwargs["phase_observer"]

        async def ask(self, _input, options):
            assert options.enforce_result_limit is False
            assert options.persist_query is False
            self.observer("schema", ctx)
            self.observer("filter", ctx)
            self.observer("generate", ctx)
            self.observer("validate", ctx)
            self.observer("execute", ctx)
            yield AskSqlGeneratedEvent(type="sql_generated", sql="SELECT 1")
            yield AskResultEvent(
                type="result",
                success=True,
                sql="SELECT 1",
                rows=[(1,)],
                columns=["value"],
                row_count=1,
                execution_time_ms=1.0,
                llm_calls=1,
                total_tokens=10,
            )

        def abandon(self, _session_id):
            raise AssertionError("Successful runs have no session")

    runner = _rdst_runner(tmp_path)
    cases, failures = runner.preflight_gold([_case()])

    with patch("devtools.ask_benchmark.runner.AskService", FakeAskService):
        result = runner._run_rdst(cases[0], _Adapter(_spec()))

    assert failures == {}
    assert result["outcome"] == "correct"
    assert result["diagnostics"]["ask_service_phases"] == [
        "schema",
        "filter",
        "generate",
        "validate",
        "execute",
    ]


def test_rdst_evidence_uses_same_first_class_context_as_direct(
    tmp_path: Path,
):
    ctx = _rdst_context()
    ctx.schema_source = SchemaSource.SEMANTIC

    class FakeAskService:
        def __init__(self, **kwargs):
            self.observer = kwargs["phase_observer"]

        async def ask(self, input_data, _options):
            assert input_data.question == _case().question
            assert input_data.provided_context == _case().evidence
            ctx.provided_context = input_data.provided_context
            self.observer("schema", ctx)
            self.observer("generate", ctx)
            yield AskResultEvent(
                type="result",
                success=True,
                sql="SELECT 1",
                rows=[(1,)],
                columns=["value"],
                row_count=1,
                execution_time_ms=1.0,
                llm_calls=1,
                total_tokens=10,
            )

        def abandon(self, _session_id):
            raise AssertionError("Successful runs have no session")

    runner = _rdst_runner(tmp_path, context_mode=ContextMode.EVIDENCE)
    runner.preflight_gold([_case()])
    with (
        patch("devtools.ask_benchmark.runner.AskService", FakeAskService),
        patch.object(SemanticLayerManager, "exists", return_value=True),
    ):
        result = runner._run_rdst(_case(), _Adapter(_spec()))

    assert result["outcome"] == "correct"
    assert result["diagnostics"]["provided_context_present"] is True
    assert result["diagnostics"]["provided_context_chars"] == len(_case().evidence)


def test_rdst_auto_mode_reaches_ask_service_as_non_interactive(tmp_path: Path):
    ctx = _rdst_context()

    class FakeAskService:
        def __init__(self, **kwargs):
            self.observer = kwargs["phase_observer"]

        async def ask(self, _input, options):
            assert options.no_interactive is True
            self.observer("schema", ctx)
            self.observer("generate", ctx)
            yield AskResultEvent(
                type="result",
                success=True,
                sql="SELECT 1",
                rows=[(1,)],
                columns=["value"],
                row_count=1,
                execution_time_ms=1.0,
                llm_calls=1,
                total_tokens=10,
            )

        def abandon(self, _session_id):
            raise AssertionError("Auto mode must not leave a session")

    runner = _rdst_runner(tmp_path, InteractionMode.AUTO)
    runner.preflight_gold([_case()])
    with patch("devtools.ask_benchmark.runner.AskService", FakeAskService):
        result = runner._run_rdst(_case(), _Adapter(_spec()))

    assert result["outcome"] == "correct"


def test_rdst_runner_binds_compact_semantic_formatter(tmp_path: Path):
    ctx = _rdst_context()
    ctx.schema_source = SchemaSource.SEMANTIC
    configured_formatter = None

    class FakeAskService:
        def __init__(self, **kwargs):
            nonlocal configured_formatter
            configured_formatter = kwargs["diagnostic_schema_formatter_fn"]
            self.observer = kwargs["phase_observer"]

        async def ask(self, _input, _options):
            self.observer("schema", ctx)
            yield AskResultEvent(
                type="result",
                success=True,
                sql="SELECT 1",
                rows=[(1,)],
                columns=["value"],
                row_count=1,
                execution_time_ms=1.0,
                llm_calls=1,
                total_tokens=10,
            )

        def abandon(self, _session_id):
            raise AssertionError("Successful runs have no session")

    runner = _rdst_runner(
        tmp_path,
        context_mode=ContextMode.AUTO_INIT,
        semantic_schema_format="rdst-compact-schema-v2",
    )
    runner.preflight_gold([_case()])
    with (
        patch("devtools.ask_benchmark.runner.AskService", FakeAskService),
        patch.object(SemanticLayerManager, "exists", return_value=True),
    ):
        result = runner._run_rdst(_case(), _Adapter(_spec()))

    assert result["outcome"] == "correct"
    assert configured_formatter is format_semantic_schema_compact
    assert runner.configuration_fingerprint(_spec()) != _rdst_runner(
        tmp_path / "verbose",
        context_mode=ContextMode.AUTO_INIT,
    ).configuration_fingerprint(_spec())


def test_rdst_runner_binds_candidate_accuracy_profile(tmp_path: Path):
    ctx = _rdst_context()
    configured = {}

    class FakeAskService:
        def __init__(self, **kwargs):
            configured.update(kwargs)
            self.observer = kwargs["phase_observer"]

        async def ask(self, _input, _options):
            self.observer("generate", ctx)
            yield AskResultEvent(
                type="result",
                success=True,
                sql="SELECT 1",
                rows=[(1,)],
                columns=["value"],
                row_count=1,
                execution_time_ms=1.0,
                llm_calls=1,
                total_tokens=10,
            )

        def abandon(self, _session_id):
            raise AssertionError("Successful runs have no session")

    runner = _rdst_runner(tmp_path, ask_accuracy_profile="candidate-v1")
    runner.preflight_gold([_case()])
    with patch("devtools.ask_benchmark.runner.AskService", FakeAskService):
        result = runner._run_rdst(_case(), _Adapter(_spec()))

    assert result["outcome"] == "correct"
    assert configured["correction_intent_routing_enabled"] is True
    assert configured["dual_candidate_selection_enabled"] is True
    assert configured["value_location_normalization_enabled"] is True
    assert runner.configuration_fingerprint(_spec()) != _rdst_runner(
        tmp_path / "baseline"
    ).configuration_fingerprint(_spec())


def test_rdst_runner_binds_candidate_v2_storage_repairs(tmp_path: Path):
    ctx = _rdst_context()
    configured = {}

    class FakeAskService:
        def __init__(self, **kwargs):
            configured.update(kwargs)
            self.observer = kwargs["phase_observer"]

        async def ask(self, _input, _options):
            self.observer("generate", ctx)
            yield AskResultEvent(
                type="result",
                success=True,
                sql="SELECT 1",
                rows=[(1,)],
                columns=["value"],
                row_count=1,
                execution_time_ms=1.0,
                llm_calls=1,
                total_tokens=10,
            )

        def abandon(self, _session_id):
            raise AssertionError("Successful runs have no session")

    runner = _rdst_runner(tmp_path, ask_accuracy_profile="candidate-v2")
    runner.preflight_gold([_case()])
    with patch("devtools.ask_benchmark.runner.AskService", FakeAskService):
        result = runner._run_rdst(_case(), _Adapter(_spec()))

    assert result["outcome"] == "correct"
    assert configured["correction_intent_routing_enabled"] is True
    assert configured["dual_candidate_selection_enabled"] is True
    assert configured["value_location_normalization_enabled"] is True
    assert configured["encoded_identifier_storage_enabled"] is True
    assert configured["temporal_text_storage_enabled"] is True
    assert configured["month_axis_storage_enabled"] is True
    assert configured["correction_intent_routing_intent_scope"][-3:] == (
        "encoded_identifier_storage",
        "temporal_text_storage",
        "month_axis_storage",
    )


def test_rdst_runner_scores_unnecessary_clarification_and_abandons_session(
    tmp_path: Path,
):
    ctx = _rdst_context()
    abandoned = []

    class FakeAskService:
        def __init__(self, **kwargs):
            self.observer = kwargs["phase_observer"]

        async def ask(self, _input, options):
            assert options.no_interactive is False
            self.observer("schema", ctx)
            self.observer("filter", ctx)
            self.observer("clarify", ctx)
            yield AskClarificationNeededEvent(
                type="clarification_needed",
                session_id="session-1",
                interpretations=[],
                questions=[
                    AskClarificationQuestion(
                        id="time_range",
                        question="Which period?",
                        options=["2024", "2025"],
                    )
                ],
            )

        def abandon(self, session_id):
            abandoned.append(session_id)
            return True

    runner = _rdst_runner(tmp_path)
    with patch("devtools.ask_benchmark.runner.AskService", FakeAskService):
        result = runner._run_rdst(_case(), _Adapter(_spec()))

    assert result["outcome"] == "clarification_required"
    assert result["failure_stage"] == "clarification"
    assert abandoned == ["session-1"]


def test_rdst_runner_scores_safe_auto_abstention_as_clarification_required(
    tmp_path: Path,
):
    ctx = _rdst_context()
    ctx.clarification_resolutions = [
        {
            "action": "abstain",
            "applied": False,
            "reason": "ranking_score_below_threshold",
        }
    ]

    class FakeAskService:
        def __init__(self, **kwargs):
            self.observer = kwargs["phase_observer"]

        async def ask(self, _input, options):
            assert options.no_interactive is True
            self.observer("clarify", ctx)
            yield AskErrorEvent(
                type="error",
                message="Run again interactively to choose an interpretation.",
                phase="clarify",
                code="clarification_required",
            )

        def abandon(self, _session_id):
            raise AssertionError("Auto abstention must not create a session")

    runner = _rdst_runner(tmp_path, InteractionMode.AUTO)
    runner.preflight_gold([_case()])
    with patch("devtools.ask_benchmark.runner.AskService", FakeAskService):
        result = runner._run_rdst(_case(), _Adapter(_spec()))

    assert result["outcome"] == "clarification_required"
    assert result["failure_stage"] == "clarification"
    assert result["diagnostics"]["clarification_resolutions"][0]["applied"] is False


def test_runner_repairs_unscored_transport_invocation(tmp_path: Path):
    store = ArtifactStore(tmp_path / "run")
    store.initialize({"run_id": "run"})
    spec = _spec()
    call_count = 0

    class FlakyAdapter(_Adapter):
        def query(self, **_kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise BenchmarkTransportError("temporary outage")
            return {"text": "SELECT 1"}

    runner = BenchmarkRunner(
        run_id="run",
        track=EvaluationTrack.MODEL_ONLY,
        context_mode=ContextMode.RAW,
        executor=_Executor(),
        schema_loader=_SchemaLoader(),
        artifact_store=store,
        filter_spec=spec,
        filter_aliases=(),
        semantic_dir=tmp_path / "semantic",
        adapter_factory=lambda model, _aliases: FlakyAdapter(model),
    )
    cases, _ = runner.preflight_gold([_case()])

    first = runner.run(cases, [spec])
    second = runner.run(cases, [spec])

    assert first[0]["scored"] is False
    assert second[1]["scored"] is True
    assert second[0]["attempt_key"] == second[1]["attempt_key"]
    assert store.completed_keys() == {second[1]["attempt_key"]}

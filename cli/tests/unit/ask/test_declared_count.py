"""Count correction regressions with fixed language witnesses and fake SQL results."""

import asyncio
from copy import deepcopy
from decimal import Decimal
import json

import pytest
import sqlglot
from sqlglot import exp

from features.ask.declared_count_language import (
    BINDING_PURPOSE,
    REQUEST_PURPOSE,
    build_binding_arguments,
    build_request_arguments,
    restore_excerpt,
    validate_binding,
    validate_request,
)
from features.ask.declared_count_planner import plan_count
from features.ask.declared_count_schema import count_schema
from features.ask.declared_count_wrapper import build_wrapper
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ColumnInfo, ExecutionResult, SchemaInfo, TableInfo
from features.ask.events import AskResultEvent
from features.ask.models import AskInput, AskOptions
import features.ask.service as service_module
from features.ask.service import AskService, _snapshot_correction_state
from features.schema.semantic_models import Relationship


QUESTION = (
    "For active learners, what is the average number of certificates linked "
    "to each learner by current links?"
)
SQL = (
    "SELECT AVG(certificate_count) AS mean_certificates FROM ("
    "SELECT p.learner_id, COUNT(i.certificate_id) AS certificate_count "
    "FROM learners p JOIN certificate_links i ON p.learner_id = i.learner_id "
    "WHERE p.status = 'active' AND i.status = 'current' "
    "GROUP BY p.learner_id) q"
)


def schema_info(dialect="mysql"):
    columns = {
        "learners": {"learner_id": "INTEGER", "status": "VARCHAR"},
        "certificates": {"certificate_id": "INTEGER"},
        "certificate_links": {
            "link_id": "INTEGER", "learner_id": "INTEGER",
            "certificate_id": "INTEGER", "status": "VARCHAR",
        },
    }
    primary_keys = {
        "learners": "learner_id", "certificates": "certificate_id",
        "certificate_links": "link_id",
    }
    info = SchemaInfo(target="unit", db_type=dialect)
    for table, fields in columns.items():
        info.tables[table] = TableInfo(name=table, columns={
            name: ColumnInfo(name=name, data_type=kind,
                             is_primary_key=name == primary_keys[table])
            for name, kind in fields.items()
        })
    info.tables["certificate_links"].relationships = [
        Relationship(target_table="learners", relationship_type="many_to_one",
                     join_pattern="certificate_links.learner_id = learners.learner_id"),
        Relationship(target_table="certificates", relationship_type="many_to_one",
                     join_pattern="certificate_links.certificate_id = certificates.certificate_id"),
    ]
    return info


def request():
    # The second clause retains the link condition without a redundant owner tag.
    return {"classification": "entity_count_mean", "clauses": [
        {"excerpt": "For active learners", "roles": ["parent_unit", "parent_filter"]},
        {"excerpt": QUESTION.split(", ", 1)[1],
         "roles": ["aggregate", "parent_unit", "child_unit", "association"]},
    ]}


def binding(catalog):
    return {"decision": "complete", "parent_relation_id": catalog["parent"]["id"],
            "association_relation_id": catalog["association"]["id"],
            "child_option_id": catalog["counted_child_option_id"],
            "clause_bindings": [
                {"clause_index": 0, "predicate_ids": catalog["parent_predicate_ids"],
                 "parent_role_ids": []},
                {"clause_index": 1, "predicate_ids": catalog["association_predicate_ids"],
                 "parent_role_ids": catalog["present_parent_role_ids"]},
            ]}


def context():
    return Ask3Context(question=QUESTION, target="unit", db_type="mysql",
        schema_info=schema_info(), target_config={}, sql=SQL,
        execution_result=ExecutionResult(columns=["original_label"],
                                        rows=[[Decimal("3")]], row_count=1))


def plan(sql=SQL, dialect="mysql"):
    return plan_count(sql, count_schema(schema_info(dialect), dialect), dialect)


class FixedLLM:
    def __init__(self, values):
        self.values = list(values)
        self.calls = []

    def generate_response(self, **arguments):
        self.calls.append(arguments)
        value = self.values.pop(0)
        return {"response": value if isinstance(value, str) else json.dumps(value),
                "model": "unit-test", "tokens_used": 7}


def proof_reply(wrapper):
    # Two parents, six incidences and four distinct parent/certificate pairs.
    # These fixed values exercise acceptance; no database evidence is claimed.
    values = {
        "selected_parent_count": 2, "grouped_parent_count": 2,
        "unique_grouped_parent_count": 2, "omitted_selected_parents": 0,
        "unexpected_grouped_parents": 0, "duplicate_parent_groups": 0,
        "selected_incidence_rows": 6, "selected_null_child_keys": 0,
        "nonnull_child_orphans": 0, "ambiguous_child_reference_rows": 0,
        "matched_child_reference_rows": 6, "raw_count_total": 6,
        "distinct_parent_child_total": 4, "matched_parent_child_total": 4,
        "per_parent_key_comparison_mismatches": 0,
        "per_parent_count_inconsistencies": 0, "duplicate_reference_difference": 2,
        "raw_native_average": Decimal("3"), "distinct_native_average": Decimal("2"),
        "candidate_native_scalar": Decimal("2"),
    }
    for aliases in wrapper["role_columns"].values():
        values.update({alias: 6 if counter in {"reference_rows", "matched_rows"} else 0
                       for counter, alias in aliases.items()})
    return {"success": True, "error": None, "columns": wrapper["columns"],
            "rows": [[values[name] for name in wrapper["columns"]]],
            "row_count": 1, "complete": True, "truncated": False}


def invoke(ctx, llm, execute):
    service = AskService(llm_manager=llm, db_executor=execute,
                         declared_count_enabled=True)
    return asyncio.run(service._apply_declared_count(ctx))


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
def test_exact_insertion_preserves_other_sql_and_unicode_offsets(dialect):
    sql = SQL.replace("SELECT AVG", "SELECT /* étude */ AVG") + "; -- finished"
    result = plan(sql, dialect)
    assert result["supported"] and result["semantic_acceptance"] is False
    offset = result["insertion"]["character_offset"]
    assert result["insertion"]["utf8_byte_offset"] == len(sql[:offset].encode()) > offset
    assert result["candidate_sql"] == sql[:offset] + "DISTINCT " + sql[offset:]
    before = sqlglot.parse_one(SQL, read=dialect)
    after = sqlglot.parse_one(plan(SQL, dialect)["candidate_sql"], read=dialect)
    count = after.find(exp.Count)
    assert isinstance(count.this, exp.Distinct)
    count.set("this", count.this.expressions[0].copy())
    assert after == before


@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
@pytest.mark.parametrize("sql", [
    SQL.replace("SELECT p.learner_id,", "SELECT p.learner_id AS certificate_count,"),
    SQL.replace(") q", ") q JOIN learners x ON x.learner_id = q.learner_id"),
    SQL.replace("AVG(certificate_count)", "AVG(other.certificate_count)"),
    SQL.replace("SELECT AVG", "SELECT /*!100100 DISTINCT */ AVG"),
    SQL + " /*m!100100 UNION SELECT 0 */",
])
def test_ambiguous_alias_extra_source_and_executable_comments_abstain(dialect, sql):
    assert not plan(sql, dialect)["supported"]


def test_payload_preserves_complete_question_witness_and_host_owner_trace():
    catalog = plan()["catalog"]
    question_arguments = build_request_arguments(QUESTION)
    assert set(json.loads(question_arguments["prompt"])) == {"effective_question", "contract"}
    assert question_arguments["purpose"] == REQUEST_PURPOSE
    checked = validate_request(QUESTION, request())
    assert checked["valid"] and checked["eligible"]
    arguments = build_binding_arguments(QUESTION, checked["value"], catalog)
    payload = json.loads(arguments["prompt"])
    assert payload["requested_contract"] == request()
    assert payload["effective_question"] == QUESTION
    assert arguments["purpose"] == BINDING_PURPOSE
    result = validate_binding(QUESTION, request(), binding(catalog), catalog)
    assert result["eligible"] and result["native_acceptance"] is False
    assert [item["predicates"][0]["owner"] for item in result["host_clause_predicate_owners"]] == ["parent", "association"]


@pytest.mark.parametrize("defect", ["missing_predicate", "duplicate_predicate", "missing_clause", "bool_index", "unknown_child", "missing_endpoint"])
def test_incomplete_binding_cannot_authorize_native_proof(defect):
    catalog = plan()["catalog"]
    value = deepcopy(binding(catalog))
    if defect == "missing_predicate": value["clause_bindings"][1]["predicate_ids"] = []
    if defect == "duplicate_predicate": value["clause_bindings"][0]["predicate_ids"] += catalog["association_predicate_ids"]
    if defect == "missing_clause": value["clause_bindings"].pop()
    if defect == "bool_index": value["clause_bindings"][0]["clause_index"] = False
    if defect == "unknown_child": value["child_option_id"] = "unknown"
    if defect == "missing_endpoint": value["clause_bindings"][1]["parent_role_ids"] = []
    assert not validate_binding(QUESTION, request(), value, catalog)["eligible"]


def test_metadata_retains_absent_endpoint_but_rejects_incomplete_child_key():
    info = schema_info()
    links = info.tables["certificate_links"]
    links.columns["reviewer_id"] = ColumnInfo(name="reviewer_id", data_type="INTEGER")
    links.relationships.append(Relationship(target_table="learners", relationship_type="many_to_one",
        join_pattern="certificate_links.reviewer_id = learners.learner_id"))
    result = plan_count(SQL, count_schema(info, "mysql"))
    assert result["supported"]
    assert len(result["catalog"]["parent_roles"]) == 2
    assert len(result["catalog"]["present_parent_role_ids"]) == 1
    info.tables["certificates"].columns["revision"] = ColumnInfo(
        name="revision", data_type="INTEGER", is_primary_key=True)
    assert not plan_count(SQL, count_schema(info, "mysql"))["supported"]


def test_metadata_rejects_relationship_predicate_disguised_as_key():
    info = schema_info()
    info.tables["certificate_links"].relationships[0].join_pattern += " AND 1=1"
    with pytest.raises(ValueError):
        count_schema(info, "mysql")


def test_fresh_combined_result_commits_once_and_keeps_output_label():
    ctx = context()
    wrapper = build_wrapper(ctx.sql, count_schema(ctx.schema_info, "mysql"))
    llm = FixedLLM([request(), binding(wrapper["plan"]["catalog"])])
    calls = []

    def execute(sql, config):
        calls.append(sql)
        assert sql == wrapper["wrapper_sql"]
        assert ctx.count_native["reservation"]["bounds"]["max_data_statements"] == 1
        return proof_reply(wrapper)

    invoke(ctx, llm, execute)
    assert calls == [wrapper["wrapper_sql"]]
    assert [call["purpose"] for call in llm.calls] == [REQUEST_PURPOSE, BINDING_PURPOSE]
    assert len(ctx.llm_calls) == 2 and ctx.total_tokens == 14
    assert ctx.sql == wrapper["plan"]["candidate_sql"]
    assert ctx.execution_result.rows == [[Decimal("2")]]
    assert ctx.execution_result.columns == ["original_label"]
    assert ctx.declared_count["status"] == "normalized"
    restored = Ask3Context.from_dict(ctx.to_dict())
    assert restored.declared_count == ctx.declared_count
    assert restored.count_native == ctx.count_native
    assert restored.count_native_probe_diagnostics == ctx.count_native_probe_diagnostics


@pytest.mark.parametrize("defect", ["incomplete", "row_count", "wrong_scalar_type", "omitted_parent", "orphan_endpoint", "bool_counter"])
def test_failed_native_evidence_restores_complete_original_state(defect):
    ctx = context()
    original = _snapshot_correction_state(ctx)
    wrapper = build_wrapper(ctx.sql, count_schema(ctx.schema_info, "mysql"))
    llm = FixedLLM([request(), binding(wrapper["plan"]["catalog"])])
    calls = []

    def execute(sql, config):
        calls.append(sql)
        reply = proof_reply(wrapper)
        if defect == "incomplete": reply["truncated"] = True
        if defect == "row_count": reply["row_count"] = 2
        if defect == "wrong_scalar_type": reply["rows"][0][wrapper["columns"].index("candidate_native_scalar")] = 2
        if defect == "omitted_parent": reply["rows"][0][wrapper["columns"].index("omitted_selected_parents")] = 1
        if defect == "bool_counter": reply["rows"][0][wrapper["columns"].index("selected_null_child_keys")] = False
        if defect == "orphan_endpoint":
            alias = next(iter(wrapper["role_columns"].values()))["orphan_keys"]
            reply["rows"][0][wrapper["columns"].index(alias)] = 1
        return reply

    invoke(ctx, llm, execute)
    assert len(calls) == 1
    assert not ctx.count_native["may_adopt"]
    assert _snapshot_correction_state(ctx) == original


@pytest.mark.parametrize("response", [
    '{"classification":"entity_count_mean","classification":"uncertain"}',
    'not JSON',
    {"classification": "occurrence_count_mean", "clauses": request()["clauses"]},
])
def test_model_error_or_occurrence_count_stops_without_native_retry(response):
    ctx = context()
    original = _snapshot_correction_state(ctx)
    llm = FixedLLM([response])
    calls = []
    invoke(ctx, llm, lambda *args: calls.append(args))
    assert calls == [] and len(llm.calls) == len(ctx.llm_calls) == 1
    assert _snapshot_correction_state(ctx) == original


def test_disabled_default_keeps_empty_diagnostics_out_of_snapshots():
    assert AskService()._declared_count_enabled is False
    snapshot = context().to_dict()
    assert not {"declared_count", "count_native", "count_native_probe_diagnostics"} & snapshot.keys()


@pytest.mark.parametrize("question,supplied,expected", [
    ("Which tools not borrowed this week remain?", "tools ... remain",
     "tools not borrowed this week remain"),
    ("¿Cuántas semillas nuevas de cada huerto quedan?", "semillas…quedan",
     "semillas nuevas de cada huerto quedan"),
    ("Count red or blue kites kept by each club in winter.", "red…kites ... winter",
     "red or blue kites kept by each club in winter"),
    ("The maker’s tools in storage remain listed.", "maker's ... remain listed",
     "maker’s tools in storage remain listed"),
    ("Print A...B and A...B literally.", "A...B", "A...B"),
    ("A red B and A blue B", "A ... B", None),
    ("A red B blue B green C", "A ... B ... C", None),
    ("A red B", "... B", None),
    ("A red B blue C", "A ... invented ... C", None),
])
def test_excerpt_restoration_preserves_unique_original_text(question, supplied, expected):
    restored = restore_excerpt(question, supplied)
    assert restored == expected
    if restored is not None:
        start = question.index(restored)
        byte_start = len(question[:start].encode())
        byte_end = len(question[:start + len(restored)].encode())
        assert question.encode()[byte_start:byte_end].decode() == restored
        assert restore_excerpt(question, restored) == restored


def test_expanded_connective_does_not_change_unsupported_classification():
    question = "Count children on the left AND NOT the right for every parent."
    excerpt = restore_excerpt(question, "children ... for every parent")
    assert excerpt == "children on the left AND NOT the right for every parent"
    checked = validate_request(question, {
        "classification": "unsupported_aggregate_or_population",
        "clauses": [{"excerpt": excerpt, "roles": ["association", "unsupported_operation"]}],
    })
    assert checked["valid"] and not checked["eligible"]


@pytest.mark.parametrize("entry", ["ask", "resume"])
@pytest.mark.parametrize("enabled", [False, True])
def test_public_ask_and_resume_use_default_off_final_correction_tail(monkeypatch, entry, enabled):
    ctx = context()
    wrapper = build_wrapper(SQL, count_schema(ctx.schema_info, "mysql"))
    llm = FixedLLM([request(), binding(wrapper["plan"]["catalog"])])
    generations, original_executions, native_executions, snapshots = [], [], [], []

    def native_execute(sql, config):
        native_executions.append(sql)
        assert sql == wrapper["wrapper_sql"]
        return proof_reply(wrapper)

    sessions = {"pending": service_module._PendingAskSession(
        context=ctx, persist_query=False, clarification_context={},
        raise_unexpected_errors=True)}
    service = AskService(
        llm_manager=llm, db_executor=native_execute, session_store=sessions,
        persist_queries=False, declared_count_enabled=enabled,
        explicit_ratio_normalization_enabled=False,
        scalar_derived_metric_normalization_enabled=False,
        all_rows_aggregate_normalization_enabled=False,
        extremum_entity_normalization_enabled=False,
        unbounded_categorical_normalization_enabled=False,
        shared_entity_scope_normalization_enabled=False,
        phase_observer=lambda phase, current: snapshots.append((phase, deepcopy(current.to_dict()))),
    )

    async def load_config(target):
        return target, {"engine": "mysql", "host": "unit-test"}

    def load_schema(current, presenter, manager):
        current.schema_info = schema_info()
        current.schema_formatted = "Learners, certificates and their links."
        return current

    def generate(current, presenter, manager):
        generations.append(current.refined_question or current.question)
        current.sql = current.generated_sql = SQL
        return current

    def execute_original(current, presenter, executor):
        original_executions.append(current.sql)
        current.execution_result = ExecutionResult(
            columns=["original_label"], rows=[[Decimal("3")]], row_count=1)
        return current

    monkeypatch.setattr(service, "_load_config", load_config)
    monkeypatch.setattr(service, "_detect_ambiguities", lambda current: (current, [], []))
    monkeypatch.setattr(service_module, "load_schema", load_schema)
    monkeypatch.setattr(service_module, "generate_sql", generate)
    monkeypatch.setattr(service_module, "validate_sql", lambda current, presenter: current)
    monkeypatch.setattr(service_module, "execute_query", execute_original)

    async def collect():
        stream = (service.ask(AskInput(question=QUESTION, target="unit"),
                              AskOptions(no_interactive=True, persist_query=False,
                                         raise_unexpected_errors=True))
                  if entry == "ask" else service.resume("pending", {}))
        return [event async for event in stream]

    events = asyncio.run(collect())
    result = events[-1]
    assert isinstance(result, AskResultEvent)
    assert generations == [QUESTION] and original_executions == [SQL]
    assert native_executions == ([wrapper["wrapper_sql"]] if enabled else [])
    assert len(llm.calls) == (2 if enabled else 0)
    assert result.sql == (wrapper["plan"]["candidate_sql"] if enabled else SQL)
    assert result.rows == [[Decimal("2") if enabled else Decimal("3")]]
    final_execute = [saved for phase, saved in snapshots if phase == "execute"][-1]
    if enabled:
        assert final_execute["declared_count"]["status"] == "normalized"
        assert final_execute["count_native"]["logical_candidate_invocations"] == 1
    else:
        assert "declared_count" not in final_execute and "count_native" not in final_execute

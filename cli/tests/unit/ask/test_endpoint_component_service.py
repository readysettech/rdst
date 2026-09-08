import asyncio, json
from types import SimpleNamespace as S
import pytest
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import ExecutionResult
from features.ask.service import AskService
from features.ask.endpoint_component import plan_endpoint_component
from features.ask.value_probe import create_endpoint_component_probe_executor

SQL = (
    "SELECT COUNT(*) AS total FROM connections WHERE node_id = '12' OR node_id2 = '12'"
)
Q = "Count undirected edges incident to local node 12 across all graphs."
ROWS = []
for namespace, n in [("G1", 4), ("G1", 6), ("G2", 8)]:
    e = f"{namespace}_{n}"
    a = f"{namespace}_12"
    b = f"{namespace}_{n}"
    ROWS.extend([[e, a, b], [e, b, a]])


def schema():
    return S(
        tables={
            "connections": S(
                name="connections",
                columns={
                    k: S(name=k, data_type="varchar(50)")
                    for k in ["node_id", "node_id2", "edge_id"]
                },
            )
        }
    )


def context(dialect="mysql"):
    return Ask3Context(
        question=Q,
        target="network",
        db_type=dialect,
        target_config={"engine": dialect},
        schema_info=schema(),
        sql=SQL,
        execution_result=ExecutionResult(rows=[[0]], columns=["total"], row_count=1),
        enforce_result_limit=False,
    )


class Decision:
    def __init__(self, extra=False):
        self.extra = extra

    def generate_response(self, **kw):
        if kw["purpose"] == "endpoint_request_routing":
            value = {
                "status": "clear",
                "count_unit": "undirected_relationships",
                "result_shape": "scalar_count",
                "endpoint_number": "12",
                "count_phrase": "Count undirected edges",
                "conditions": [
                    {"role": "endpoint_number", "phrase": "local node 12"},
                    {"role": "all_graphs", "phrase": "across all graphs"},
                ],
            }
            if self.extra:
                value["conditions"].append(
                    {"role": "edge_property", "phrase": "undirected"}
                )
        else:
            assert kw["purpose"] == "endpoint_component_routing"
            value = {
                "decision": "activate",
                "count_unit": "undirected_edges",
                "endpoint_reference": "local_numeric_component",
                "requested_number": "12",
                "all_graphs": True,
                "no_unrepresented_conditions": True,
                "question_quote": Q,
            }
        return {"response": json.dumps(value), "model": "scripted", "tokens_used": 10}


@pytest.mark.parametrize("dialect", ["mysql", "postgresql"])
@pytest.mark.parametrize(
    "failure",
    [
        None,
        "wrong-count",
        "partial-proof",
        "partial-candidate",
        "missing-reverse",
        "proof-error",
        "candidate-error",
    ],
)
def test_complete_edge_proof_and_restoration(dialect, failure):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        first = len(calls) == 1
        rows = ROWS if first else [[6 if failure == "wrong-count" else 3]]
        if first and failure == "missing-reverse":
            rows = ROWS[:-1]
        partial = (first and failure == "partial-proof") or (
            not first and failure == "partial-candidate"
        )
        error = (first and failure == "proof-error") or (
            not first and failure == "candidate-error"
        )
        return {
            "success": not error,
            "rows": rows,
            "columns": ["edge_id", "node_id", "node_id2"] if first else ["total"],
            "truncated": partial,
            "error": "injected" if error else None,
        }

    ctx = context(dialect)
    original_result = ctx.execution_result
    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(), db_executor=execute, endpoint_component_enabled=True
        )._apply_endpoint_component(ctx)
    )
    assert len(calls) == (
        1 if failure in ["partial-proof", "missing-reverse", "proof-error"] else 2
    )
    expected_phases = (
        {"endpoint_request_routing"}
        if failure in ["partial-proof", "missing-reverse", "proof-error"]
        else {"endpoint_request_routing", "endpoint_component_routing"}
    )
    assert len(ctx.llm_calls) == len(expected_phases)
    assert {x["phase"] for x in ctx.llm_calls} == expected_phases
    assert ctx.endpoint_component_probe_diagnostics["calls"] == len(calls)
    if failure:
        assert ctx.sql == SQL and ctx.execution_result == original_result
        assert ctx.endpoint_component["status"] in ["unchanged", "reverted"]
    else:
        assert ctx.endpoint_component["status"] == "normalized"
        assert ctx.sql == plan_endpoint_component(SQL, dialect, schema()).candidate_sql
        assert ctx.execution_result.rows == [[3]]
    restored = Ask3Context.from_dict(ctx.to_dict())
    assert restored.endpoint_component == ctx.endpoint_component
    assert (
        restored.endpoint_component_probe_diagnostics
        == ctx.endpoint_component_probe_diagnostics
    )


def test_unrepresented_condition_avoids_sql_and_base_router():
    def forbidden(*a):
        raise AssertionError("No database call expected")

    ctx = context()
    ctx = asyncio.run(
        AskService(
            llm_manager=Decision(extra=True), db_executor=forbidden
        )._apply_endpoint_component(ctx)
    )
    assert ctx.sql == SQL and ctx.execution_result.rows == [[0]]
    assert (
        ctx.endpoint_component["reason"] == "model-abstained"
        and len(ctx.llm_calls) == 1
    )


def test_read_only_call_budget_and_timeout(monkeypatch):
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {"success": True, "rows": [[1]]}

    ctx = context()
    probe = create_endpoint_component_probe_executor(ctx, execute)
    assert not probe("DELETE FROM connections", {})["success"]
    assert probe("SELECT 1", {})["success"]
    assert not probe("SELECT 1", {})["success"]
    assert len(calls) == 1
    import features.ask.value_probe as module

    ticks = iter([100.0, 103.0])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks))
    probe = create_endpoint_component_probe_executor(context(), execute)
    assert probe("SELECT 1", {})["error_kind"] == "probe_timeout" and len(calls) == 1


def test_database_budget_excludes_model_wait(monkeypatch):
    import features.ask.value_probe as module

    now = [100.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    ctx = context()
    probe = create_endpoint_component_probe_executor(
        ctx, lambda *a: {"success": True, "rows": [[1]]}
    )
    assert probe("SELECT 1", {})["success"]
    now[0] = 1000.0
    assert probe("SELECT 1", {})["success"]
    assert not probe("SELECT 1", {})["success"]
    assert ctx.endpoint_component_probe_diagnostics["calls"] == 2
    assert all(
        d["timeout_seconds"] == 2
        for d in ctx.endpoint_component_probe_diagnostics["queries"]
    )


@pytest.mark.parametrize(
    "change",
    [
        {"provided_context": "extra"},
        {"conversation_context": "extra"},
        {"sql": "SELECT 1"},
        {"execution_result": None},
        {
            "execution_result": ExecutionResult(
                rows=[[1]], columns=["total"], row_count=1
            )
        },
    ],
)
def test_unsupported_inputs_make_no_calls(change):
    class Forbidden:
        def generate_response(self, **kw):
            raise AssertionError("No model call expected")

    ctx = context()
    for k, v in change.items():
        setattr(ctx, k, v)
    ctx = asyncio.run(
        AskService(llm_manager=Forbidden())._apply_endpoint_component(ctx)
    )
    assert ctx.endpoint_component["status"] == "unchanged"


def test_policy_annotation_is_recorded_before_complete_count_proof():
    class PolicyDecision(Decision):
        def generate_response(self, **kw):
            if kw["purpose"] == "endpoint_count_policy_routing":
                return {
                    "response": json.dumps(
                        {
                            "same_count_unit": True,
                            "verbatim_count_phrase": "Count undirected edges",
                            "other_conditions": [
                                {"index": 2, "kind": "once_per_undirected_edge"}
                            ],
                        }
                    ),
                    "model": "scripted",
                    "tokens_used": 10,
                }
            response = super().generate_response(**kw)
            if kw["purpose"] == "endpoint_request_routing":
                value = json.loads(response["response"])
                value["conditions"].append(
                    {"role": "other", "phrase": "Count each edge once"}
                )
                response["response"] = json.dumps(value)
            return response

    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": ROWS if len(calls) == 1 else [[3]],
            "columns": ["edge_id", "node_id", "node_id2"]
            if len(calls) == 1
            else ["total"],
        }

    ctx = context()
    ctx.question += " Count each edge once."
    ctx = asyncio.run(
        AskService(
            llm_manager=PolicyDecision(), db_executor=execute
        )._apply_endpoint_component(ctx)
    )
    assert ctx.endpoint_component["status"] == "normalized"
    assert ctx.endpoint_component["routing"]["policy"]["status"] == "resolved"
    assert len(calls) == 2 and len(ctx.llm_calls) == 3
    assert {c["phase"] for c in ctx.llm_calls} == {
        "endpoint_request_routing",
        "endpoint_count_policy_routing",
        "endpoint_component_routing",
    }


@pytest.mark.parametrize(
    "stage",
    [
        "endpoint_request_routing",
        "endpoint_count_policy_routing",
        "endpoint_component_routing",
    ],
)
def test_model_output_failure_restores_original_before_candidate(stage):
    class Broken(Decision):
        def generate_response(self, **kw):
            if kw["purpose"] == stage:
                raise ValueError("Retained malformed model output")
            response = super().generate_response(**kw)
            if (
                stage == "endpoint_count_policy_routing"
                and kw["purpose"] == "endpoint_request_routing"
            ):
                value = json.loads(response["response"])
                value["conditions"].append(
                    {"role": "other", "phrase": "Count each edge once"}
                )
                response["response"] = json.dumps(value)
            return response

    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": ROWS,
            "columns": ["edge_id", "node_id", "node_id2"],
        }

    ctx = context()
    ctx.question += " Count each edge once."
    original = ctx.execution_result
    ctx = asyncio.run(
        AskService(llm_manager=Broken(), db_executor=execute)._apply_endpoint_component(
            ctx
        )
    )
    assert ctx.sql == SQL and ctx.execution_result == original
    assert ctx.endpoint_component["status"] == "reverted"
    assert len(calls) == (1 if stage == "endpoint_component_routing" else 0)
    assert "candidate_sql" not in ctx.endpoint_component


@pytest.mark.parametrize("prefix", ["composite_", "opaque-key-"])
def test_service_proves_opaque_edge_keys_before_routing(prefix):
    data = [[prefix + edge + "_12", left, right] for edge, left, right in ROWS]

    class VerifiedDecision(Decision):
        def generate_response(self, **kw):
            if kw["purpose"] == "endpoint_component_routing":
                facts = json.loads(kw["prompt"])["native_storage_facts"]
                assert "opaque" in facts["edge_identifier_format"]
                assert facts["each_edge_connects_endpoints_in_one_namespace"] is True
            return super().generate_response(**kw)

    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": data if len(calls) == 1 else [[3]],
            "columns": ["edge_id", "node_id", "node_id2"]
            if len(calls) == 1
            else ["total"],
        }

    ctx = asyncio.run(
        AskService(
            llm_manager=VerifiedDecision(), db_executor=execute
        )._apply_endpoint_component(context())
    )
    assert ctx.endpoint_component[
        "status"
    ] == "normalized" and ctx.execution_result.rows == [[3]]
    assert len(calls) == 2

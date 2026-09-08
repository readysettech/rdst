import asyncio, copy, json
import pytest
from features.ask.engine.ask3.context import Ask3Context
from features.ask.engine.ask3.types import (
    SchemaInfo,
    TableInfo,
    ColumnInfo,
    ExecutionResult,
)
from features.ask.service import AskService
from features.ask.value_probe import (
    create_count_name_domain_executor,
    create_count_name_candidate_executor,
)

SQL = "SELECT (SELECT COUNT(*) FROM orders WHERE vendor='Acme') - (SELECT COUNT(*) FROM orders WHERE vendor='Beta') AS difference"
Q = "Acme refers to Acme Supply. How many more orders are from Acme than Beta?"


def context():
    return Ask3Context(
        question=Q,
        target="fixture",
        db_type="mysql",
        target_config={"engine": "mysql"},
        sql=SQL,
        generated_sql=SQL,
        enforce_result_limit=False,
        schema_info=SchemaInfo(
            target="fixture",
            db_type="mysql",
            tables={
                "orders": TableInfo(
                    name="orders",
                    columns={
                        "vendor": ColumnInfo(name="vendor", data_type="varchar"),
                        "id": ColumnInfo(name="id", data_type="int"),
                    },
                )
            },
        ),
        execution_result=ExecutionResult(
            rows=[[-3]], columns=["difference"], row_count=1
        ),
    )


class Decisions:
    def __init__(self, guard=True, identity=True):
        self.guard = guard
        self.identity = identity
        self.calls = []

    def generate_response(self, **kw):
        purpose = kw["purpose"]
        self.calls.append(purpose)
        if purpose == "request_entity_expansion":
            data = json.loads(kw["prompt"])
            assert "stored_full_label" not in data
            v = dict(
                polarity="positive" if self.guard else "excluded",
                scope="single_named_entity",
                name_source="explicit_in_question",
                expanded_name="Acme Supply",
                reference_excerpt=Q,
            )
        else:
            assert purpose == "named_entity_label_completion"
            v = dict(
                reference_kind="named_entity",
                name_relation="same_entity_full_name",
                positive_reference="yes" if self.identity else "no",
                source_excerpt="Acme",
            )
        return {"response": json.dumps(v), "model": "scripted"}


def run(ctx, manager, executor):
    return asyncio.run(
        AskService(
            llm_manager=manager, db_executor=executor
        )._apply_count_name_completion(ctx)
    )


def test_exact_count_proof_acceptance_and_serialization():
    ctx = context()
    manager = Decisions()
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {
            "success": True,
            "rows": [[b"Acme Supply", 0, 0], [b"Beta", 0, 1]]
            if len(calls) == 1
            else [[0, 3, 2]]
            if len(calls) == 2
            else [[-1]],
            "columns": ["difference"],
        }

    run(ctx, manager, execute)
    assert (
        ctx.count_name_completion["status"] == "normalized"
        and ctx.sql == SQL.replace("'Acme'", "'Acme Supply'")
        and ctx.generated_sql == SQL
    )
    assert (
        ctx.execution_result.rows == [[-1]]
        and len(calls) == 3
        and len(ctx.llm_calls) == 2
    )
    assert manager.calls == [
        "request_entity_expansion",
        "named_entity_label_completion",
    ]
    restored = Ask3Context.from_dict(ctx.to_dict())
    for name in (
        "count_name_completion",
        "count_name_domain_diagnostics",
        "count_name_candidate_diagnostics",
    ):
        assert getattr(restored, name) == getattr(ctx, name)


@pytest.mark.parametrize(
    "failure",
    [
        "domain-error",
        "domain-truncated",
        "domain-ambiguous",
        "proof-error",
        "proof-truncated",
        "proof-mismatch",
        "candidate-error",
        "candidate-truncated",
        "candidate-mismatch",
        "candidate-exception",
    ],
)
def test_failure_restores_complete_original(failure):
    ctx = context()
    before = copy.deepcopy(ctx.execution_result)
    calls = []

    def execute(sql, config):
        calls.append(sql)
        i = len(calls)
        if failure == "candidate-exception" and i == 3:
            raise RuntimeError("fixture")
        if (failure, i) in [
            ("domain-error", 1),
            ("proof-error", 2),
            ("candidate-error", 3),
        ]:
            return {"success": False, "error": "fixture", "rows": []}
        rows = (
            [[b"Acme Supply", 0, 0], [b"Beta", 0, 1]]
            if i == 1
            else [[0, 3, 2]]
            if i == 2
            else [[-1]]
        )
        if failure == "domain-ambiguous" and i == 1:
            rows.append([b"Acme Tools", 0, 0])
        if failure == "proof-mismatch" and i == 2:
            rows = [[1, 3, 2]]
        if failure == "candidate-mismatch" and i == 3:
            rows = [[99]]
        return {
            "success": True,
            "rows": rows,
            "columns": ["difference"],
            "truncated": (failure, i)
            in [
                ("domain-truncated", 1),
                ("proof-truncated", 2),
                ("candidate-truncated", 3),
            ],
        }

    run(ctx, Decisions(), execute)
    assert (
        ctx.count_name_completion["status"] != "normalized"
        and ctx.sql == SQL
        and ctx.execution_result == before
        and len(calls) <= 3
    )


@pytest.mark.parametrize("guard,identity,calls", [(False, True, 1), (True, False, 2)])
def test_semantic_abstention_keeps_query(guard, identity, calls):
    ctx = context()
    m = Decisions(guard, identity)
    queries = []

    def execute(sql, config):
        queries.append(sql)
        return {"success": True, "rows": [[b"Acme Supply", 0, 0], [b"Beta", 0, 1]]}

    run(ctx, m, execute)
    assert (
        ctx.sql == SQL
        and ctx.count_name_completion["status"] == "unchanged"
        and len(queries) == 1
        and len(m.calls) == calls
    )


@pytest.mark.parametrize(
    "kind",
    [
        "postgres",
        "provided",
        "conversation",
        "error",
        "truncated",
        "no-result",
        "shape",
    ],
)
def test_ineligible_never_probes(kind):
    ctx = context()
    if kind == "postgres":
        ctx.db_type = "postgres"
    elif kind == "provided":
        ctx.provided_context = "Use exact strings."
    elif kind == "conversation":
        ctx.conversation_context = "Include subsidiaries."
    elif kind == "error":
        ctx.execution_result.error = "fixture"
    elif kind == "truncated":
        ctx.execution_result.truncated = True
    elif kind == "no-result":
        ctx.execution_result = None
    elif kind == "shape":
        ctx.sql = "SELECT COUNT(*) FROM orders"

    def forbidden(*a):
        raise AssertionError("ineligible probe")

    m = Decisions()
    run(ctx, m, forbidden)
    assert not m.calls and ctx.count_name_completion["status"] == "unchanged"


@pytest.mark.parametrize(
    "factory,budget,seconds",
    [
        (create_count_name_domain_executor, 1, 2),
        (create_count_name_candidate_executor, 2, 4),
    ],
)
def test_phase_budgets_and_deadlines(monkeypatch, factory, budget, seconds):
    from features.ask import value_probe

    now = [0.0]
    monkeypatch.setattr(value_probe.time, "monotonic", lambda: now[0])
    ctx = context()
    calls = []

    def execute(sql, config):
        calls.append(sql)
        return {"success": True, "rows": [[1]]}

    bounded = factory(ctx, execute)
    for i in range(budget):
        assert bounded("SELECT 1", ctx.target_config)["success"]
    assert (
        not bounded("SELECT 1", ctx.target_config)["success"] and len(calls) == budget
    )
    bounded = factory(ctx, execute)
    now[0] = seconds + 0.01
    assert (
        not bounded("SELECT 1", ctx.target_config)["success"] and len(calls) == budget
    )
    now[0] = 0
    bounded = factory(ctx, execute)
    assert (
        not bounded("DELETE FROM orders", ctx.target_config)["success"]
        and len(calls) == budget
    )


def test_profile_default_off():
    from devtools.ask_benchmark.runner import (
        ASK_ACCURACY_PROFILES,
        ask_profile_service_flags,
    )

    assert not AskService(llm_manager=Decisions())._count_name_completion_enabled
    for p in ASK_ACCURACY_PROFILES:
        assert ask_profile_service_flags(p)["count_name_completion_enabled"] is False
